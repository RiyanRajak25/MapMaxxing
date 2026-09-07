"""Test relative radiometric normalisation against the measured net-change bias.

THE PROBLEM THIS TARGETS

Two composites of the same dry season classify different amounts of built-up -
the whole classification LEVEL shifts between dates. Speckle filters and
hysteresis cannot touch it, and per-scene voting made it worse, which is the
evidence it is systematic rather than random (scripts/evaluate_voting.py).

A systematic, scene-wide offset between two dates is exactly what relative
radiometric normalisation exists to remove, and it is the standard preprocessing
step for multi-temporal change detection. L2A surface reflectance is
atmospherically corrected but not perfectly: residual aerosol, sun angle, and
BRDF differences leave a per-date offset, and it lands hardest on blue - which
is the numerator of k.

THE METHOD

Pseudo-invariant feature normalisation, mean-sigma variant:

  1. Rank every pixel by spectral distance between the two composites and keep
     the most stable half. Those are pixels that plausibly did not change, so
     any residual difference across them is instrument and atmosphere, not
     ground truth.
  2. Per band, fit gain and offset so the target's mean and standard deviation
     over that invariant set match the reference's.
  3. Apply to the target.

Period 2 is the reference and period 1 is corrected onto it, because K_MIN=0.42
was calibrated against 2023-24 reflectances (scripts/diagnose_amcbi.py). Moving
period 2 would move the imagery out from under its own calibration.

WHAT WOULD COUNT AS SUCCESS

The same-season null test must fall toward 0% net change, and the real run must
keep a substantial signal. A correction that flattens both has just erased the
data.

Run from backend/:
    python scripts/evaluate_normalization.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services import amcbi as amcbi_service  # noqa: E402
from app.services import change_filters  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import AMCBI_BANDS, build_composite, native_projection  # noqa: E402

# Bhoj Wetland study area - the prototype's scope.
AOI_COORDS = [
    [[77.22, 23.18], [77.48, 23.18], [77.48, 23.32], [77.22, 23.32], [77.22, 23.18]]
]

SCALE = 10
CLOUD_THRESHOLD = 20

# Keep the most stable half of the scene as the invariant set. Too small and the
# fit is noisy; too large and genuinely changed ground contaminates it.
NO_CHANGE_PERCENTILE = 50

CASES = {
    "real 2017 vs 2023": (("2017-11-01", "2018-02-28"), ("2023-11-01", "2024-02-29")),
    "null: 1 year apart": (("2022-11-01", "2023-02-28"), ("2023-11-01", "2024-02-29")),
    "null: same season": (("2023-11-01", "2023-12-31"), ("2024-01-01", "2024-02-29")),
}


def invariant_mask(target: ee.Image, reference: ee.Image, aoi: ee.Geometry) -> ee.Image:
    """The most spectrally stable half of the scene."""
    magnitude = (
        target.subtract(reference).pow(2).reduce(ee.Reducer.sum()).sqrt().rename("mag")
    )
    # Take the single value positionally: the reducer's output key depends on
    # Earth Engine's naming for percentile outputs, which is not worth guessing.
    cutoff = ee.Number(
        magnitude.reduceRegion(
            ee.Reducer.percentile([NO_CHANGE_PERCENTILE]),
            aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8,
        ).values().get(0)
    )
    return magnitude.lt(ee.Image.constant(cutoff))


def fit(target: ee.Image, reference: ee.Image, aoi: ee.Geometry) -> dict:
    """Per-band gain and offset putting target on reference's radiometric scale."""
    invariant = invariant_mask(target, reference, aoi)
    paired = ee.Image.cat(
        [
            target.select(AMCBI_BANDS).rename([f"t_{b}" for b in AMCBI_BANDS]),
            reference.select(AMCBI_BANDS).rename([f"r_{b}" for b in AMCBI_BANDS]),
        ]
    ).updateMask(invariant)

    stats = paired.reduceRegion(
        ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
        aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8,
    )

    coefficients = {}
    for band in AMCBI_BANDS:
        t_mean, t_sd = ee.Number(stats.get(f"t_{band}_mean")), ee.Number(stats.get(f"t_{band}_stdDev"))
        r_mean, r_sd = ee.Number(stats.get(f"r_{band}_mean")), ee.Number(stats.get(f"r_{band}_stdDev"))
        gain = r_sd.divide(t_sd)
        coefficients[band] = (gain, r_mean.subtract(t_mean.multiply(gain)))
    return coefficients


def apply_fit(target: ee.Image, coefficients: dict) -> ee.Image:
    return ee.Image.cat(
        [
            target.select(band)
            .multiply(ee.Image.constant(gain))
            .add(ee.Image.constant(offset))
            .rename(band)
            for band, (gain, offset) in coefficients.items()
        ]
    )


def change_of(c1: ee.Image, c2: ee.Image, aoi: ee.Geometry, projection) -> dict:
    star1, k1 = amcbi_service.constraint_terms(c1)
    star2, k2 = amcbi_service.constraint_terms(c2)
    built1 = amcbi_service.built_up_with_margin(star1, k1, 0)
    built2 = amcbi_service.built_up_with_margin(star2, k2, 0)
    common = built1.mask().And(built2.mask())
    built1, built2 = built1.updateMask(common), built2.updateMask(common)

    gain, loss = change_filters.hysteresis_change(star1, k1, star2, k2)
    gain, loss = change_filters.filter_change(
        gain.updateMask(common), loss.updateMask(common), projection
    )

    px = ee.Image.pixelArea()
    stacked = (
        px.multiply(built1).rename("b1")
        .addBands(px.multiply(built2).rename("b2"))
        .addBands(px.updateMask(gain.selfMask()).rename("gain"))
        .addBands(px.updateMask(loss.selfMask()).rename("loss"))
    )
    raw = stacked.reduceRegion(
        ee.Reducer.sum(), aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8
    ).getInfo()
    return {key: (raw.get(key) or 0) / 1e6 for key in ("b1", "b2", "gain", "loss")}


def show(label: str, r: dict) -> None:
    net = (r["b2"] - r["b1"]) / r["b1"] * 100 if r["b1"] else 0
    print(f"    {label:<14}{r['b1']:>10.2f}{r['b2']:>10.2f}{r['gain']:>9.2f}{r['loss']:>9.2f}"
          f"{net:>+10.1f}")


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)
    projection = native_projection(aoi, "2023-11-01", "2024-02-29")

    print("Bhoj Wetland study area, relative radiometric normalisation")
    print(f"Invariant set: most stable {NO_CHANGE_PERCENTILE}% of pixels\n")

    for case, ((s1, e1), (s2, e2)) in CASES.items():
        c1 = build_composite(aoi, s1, e1, CLOUD_THRESHOLD, "Period 1")
        c2 = build_composite(aoi, s2, e2, CLOUD_THRESHOLD, "Period 2")

        coefficients = fit(c1, c2, aoi)
        c1_normalised = apply_fit(c1, coefficients)

        print(f"  {case}")
        print(f"    {'variant':<14}{'built p1':>10}{'built p2':>10}{'gain':>9}{'loss':>9}{'net %':>10}")
        show("baseline", change_of(c1, c2, aoi, projection))
        show("normalised", change_of(c1_normalised, c2, aoi, projection))

        resolved = ee.Dictionary(
            {band: ee.List([gain, offset]) for band, (gain, offset) in coefficients.items()}
        ).getInfo()
        fitted = "  ".join(
            f"{band} x{resolved[band][0]:.3f}{resolved[band][1]:+.4f}" for band in AMCBI_BANDS
        )
        print(f"    correction applied to period 1: {fitted}\n")

    print("  A correction near x1.000+0.0000 means the two dates were already on the")
    print("  same radiometric scale and normalisation has nothing to do.")
    print("  Success = null net % moves toward 0 while real net % survives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
