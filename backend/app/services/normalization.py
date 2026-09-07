"""Relative radiometric normalisation between the two periods.

THE BIAS THIS REMOVES

L2A is atmospherically corrected but not perfectly comparable across years.
Sen2Cor's processing baseline changed repeatedly between 2017 and 2024, and
residual aerosol, sun-angle and BRDF differences leave a per-date offset. It
lands hardest on blue, which is the numerator of k - the term with the thinnest
margin in the whole method.

MEASURED, ON GROUND THAT CANNOT HAVE CHANGED

scripts/verify_stable_core.py measures the built-up fraction over dense Bhopal
neighbourhoods that were fully built decades before 2017 and still stand. Their
built-up fraction is a fixed quantity, so any difference between dates is
classifier bias with no ground-truth component at all:

    stable core built-up fraction   2017    2023    gap
    uncorrected                     0.605   0.677   -0.071
    normalised                      0.638   0.677   -0.039

The 2017 composite under-detects built-up by about 12% relative. That is not
subtle: it inflated the Bhoj Wetland 2017->2023 growth figure from a true value
near 13% to a reported 27.4%, because the earlier date started from an
artificially low base.

Two independent routes agree on the corrected answer. Scaling the uncorrected
2017 area up by the 12% core bias predicts +13.9% growth; running the pipeline
on normalised imagery gives +13.1%.

THE METHOD

Pseudo-invariant feature normalisation, mean-sigma variant:

  1. Rank pixels by spectral distance between the two composites and keep the
     most stable NO_CHANGE_PERCENTILE. Those plausibly did not change, so
     residual differences across them are instrument and atmosphere, not ground.
  2. Per band, fit gain and offset so the target's mean and standard deviation
     over that set match the reference's.
  3. Apply to the target.

Period 2 is always the reference and period 1 is corrected onto it, because
K_MIN = 0.42 was calibrated against 2023-24 reflectances (see amcbi.py and
scripts/diagnose_amcbi.py). Moving period 2 would slide the imagery out from
under its own calibration.

LIMITS - READ BEFORE TRUSTING THE OUTPUT

Normalisation closes about 45% of the measured bias, not all of it. A residual
-0.039 gap remains on stable ground, so reported growth is still biased HIGH.
Correcting for the residual as well would put Bhoj nearer +7%. Treat the
normalised figure as an upper bound, not a point estimate.

It also cannot fix the year-to-year level instability: a one-year null test
still reports +9.4% after normalisation where reality is 2-4%.
"""

import ee

from app import config
from app.services.imagery import AMCBI_BANDS

# Share of the scene treated as unchanged when fitting the correction. Too small
# and the fit is noisy; too large and genuinely changed ground contaminates it.
NO_CHANGE_PERCENTILE = config.NO_CHANGE_PERCENTILE


def invariant_mask(
    target: ee.Image, reference: ee.Image, aoi: ee.Geometry, scale: int
) -> ee.Image:
    """The most spectrally stable share of the scene, as a 0/1 mask."""
    magnitude = (
        target.select(AMCBI_BANDS)
        .subtract(reference.select(AMCBI_BANDS))
        .pow(2)
        .reduce(ee.Reducer.sum())
        .sqrt()
        .rename("magnitude")
    )
    # Taken positionally: Earth Engine's naming for percentile outputs is not
    # worth guessing, and there is exactly one value here.
    cutoff = ee.Number(
        magnitude.reduceRegion(
            reducer=ee.Reducer.percentile([NO_CHANGE_PERCENTILE]),
            geometry=aoi,
            scale=scale,
            maxPixels=1e10,
            bestEffort=True,
            tileScale=8,
        ).values().get(0)
    )
    return magnitude.lt(ee.Image.constant(cutoff))


def normalise_to(
    target: ee.Image, reference: ee.Image, aoi: ee.Geometry, scale: int
) -> ee.Image:
    """Put `target` on `reference`'s radiometric scale, band by band.

    Everything here is lazy: the reduceRegion resolves when the caller finally
    evaluates something downstream, so this costs one extra pass, not a
    round trip per band.
    """
    invariant = invariant_mask(target, reference, aoi, scale)

    paired = ee.Image.cat(
        [
            target.select(AMCBI_BANDS).rename([f"t_{band}" for band in AMCBI_BANDS]),
            reference.select(AMCBI_BANDS).rename([f"r_{band}" for band in AMCBI_BANDS]),
        ]
    ).updateMask(invariant)

    stats = paired.reduceRegion(
        reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        geometry=aoi,
        scale=scale,
        maxPixels=1e10,
        bestEffort=True,
        # Ten bands through two reducers overruns the per-tile memory budget.
        tileScale=8,
    )

    corrected = []
    for band in AMCBI_BANDS:
        target_mean = ee.Number(stats.get(f"t_{band}_mean"))
        target_sd = ee.Number(stats.get(f"t_{band}_stdDev"))
        reference_mean = ee.Number(stats.get(f"r_{band}_mean"))
        reference_sd = ee.Number(stats.get(f"r_{band}_stdDev"))

        gain = reference_sd.divide(target_sd)
        offset = reference_mean.subtract(target_mean.multiply(gain))
        corrected.append(
            target.select(band)
            .multiply(ee.Image.constant(gain))
            .add(ee.Image.constant(offset))
            .rename(band)
        )

    return ee.Image.cat(corrected)
