"""Decide whether relative normalisation is correcting a bias or inventing one.

THE QUESTION

Normalisation moves the Bhoj 2017->2023 result from +27.4% to +13.1% - it halves
the headline. That is only legitimate if the 2017 composite really was
under-classifying built-up relative to 2023. If it was not, normalisation is
manufacturing 2017 built-up and suppressing real growth.

THE TEST

Dense urban cores that were fully built long before 2017 and still stand cannot
have grown. Their built-up FRACTION is therefore a fixed quantity, and any
difference between the two dates is pure classifier bias.

  - If 2017 reads materially LOWER than 2023 over these cores, the 2017
    composite under-classifies and normalisation is correcting a real bias.
  - If they already read the same, there is no bias to correct, and
    normalisation is destroying real signal at the growth margin instead.

Buffers of 500 m are used rather than points so the measurement covers the
mixed-pixel fabric of a real neighbourhood, not a single hand-picked rooftop.

Run from backend/:
    python scripts/verify_stable_core.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services import amcbi as amcbi_service  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import AMCBI_BANDS, build_composite  # noqa: E402

AOI_COORDS = [
    [[77.22, 23.18], [77.48, 23.18], [77.48, 23.32], [77.22, 23.32], [77.22, 23.18]]
]

SCALE = 10
CLOUD_THRESHOLD = 20
CORE_RADIUS_M = 500
NO_CHANGE_PERCENTILE = 50

PERIOD_1 = ("2017-11-01", "2018-02-28")
PERIOD_2 = ("2023-11-01", "2024-02-29")

# Dense, continuously built since decades before 2017.
STABLE_CORES = {
    "New Market / TT Nagar": [77.4000, 23.2330],
    "Old City / Chowk": [77.4020, 23.2600],
    "MP Nagar": [77.4350, 23.2330],
}


def invariant_mask(target: ee.Image, reference: ee.Image, aoi: ee.Geometry) -> ee.Image:
    magnitude = (
        target.subtract(reference).pow(2).reduce(ee.Reducer.sum()).sqrt().rename("mag")
    )
    cutoff = ee.Number(
        magnitude.reduceRegion(
            ee.Reducer.percentile([NO_CHANGE_PERCENTILE]),
            aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8,
        ).values().get(0)
    )
    return magnitude.lt(ee.Image.constant(cutoff))


def normalise(target: ee.Image, reference: ee.Image, aoi: ee.Geometry) -> ee.Image:
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
    corrected = []
    for band in AMCBI_BANDS:
        t_mean = ee.Number(stats.get(f"t_{band}_mean"))
        t_sd = ee.Number(stats.get(f"t_{band}_stdDev"))
        r_mean = ee.Number(stats.get(f"r_{band}_mean"))
        r_sd = ee.Number(stats.get(f"r_{band}_stdDev"))
        gain = r_sd.divide(t_sd)
        corrected.append(
            target.select(band)
            .multiply(ee.Image.constant(gain))
            .add(ee.Image.constant(r_mean.subtract(t_mean.multiply(gain))))
            .rename(band)
        )
    return ee.Image.cat(corrected)


def built_up(composite: ee.Image) -> ee.Image:
    star, k = amcbi_service.constraint_terms(composite)
    return amcbi_service.built_up_with_margin(star, k, 0)


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)

    c1 = build_composite(aoi, *PERIOD_1, CLOUD_THRESHOLD, "Period 1")
    c2 = build_composite(aoi, *PERIOD_2, CLOUD_THRESHOLD, "Period 2")
    c1_norm = normalise(c1, c2, aoi)

    stacked = (
        built_up(c1).rename("b_2017")
        .addBands(built_up(c1_norm).rename("b_2017_norm"))
        .addBands(built_up(c2).rename("b_2023"))
    )

    print("Built-up fraction over cores that cannot have grown since 2017")
    print(f"({CORE_RADIUS_M} m radius; 1.00 = every pixel classified built-up)\n")
    print(f"  {'core':<24}{'2017':>9}{'2017 norm':>12}{'2023':>9}{'2017-2023':>12}"
          f"{'norm-2023':>12}")

    totals = {"b_2017": 0.0, "b_2017_norm": 0.0, "b_2023": 0.0}
    for name, coords in STABLE_CORES.items():
        core = ee.Geometry.Point(coords).buffer(CORE_RADIUS_M)
        vals = stacked.reduceRegion(
            ee.Reducer.mean(), core, SCALE, maxPixels=1e9, bestEffort=True
        ).getInfo()
        if vals.get("b_2017") is None:
            print(f"  {name:<24}  (no data)")
            continue
        for key in totals:
            totals[key] += vals[key]
        print(f"  {name:<24}{vals['b_2017']:>9.3f}{vals['b_2017_norm']:>12.3f}"
              f"{vals['b_2023']:>9.3f}{vals['b_2017'] - vals['b_2023']:>+12.3f}"
              f"{vals['b_2017_norm'] - vals['b_2023']:>+12.3f}")

    n = len(STABLE_CORES)
    mean_2017 = totals["b_2017"] / n
    mean_norm = totals["b_2017_norm"] / n
    mean_2023 = totals["b_2023"] / n
    print(f"\n  {'MEAN':<24}{mean_2017:>9.3f}{mean_norm:>12.3f}{mean_2023:>9.3f}"
          f"{mean_2017 - mean_2023:>+12.3f}{mean_norm - mean_2023:>+12.3f}")

    print("\n  VERDICT")
    baseline_gap = abs(mean_2017 - mean_2023)
    normalised_gap = abs(mean_norm - mean_2023)
    if mean_2017 < mean_2023 - 0.02:
        print("  2017 under-classifies the stable core. Normalisation is correcting a")
        print("  real radiometric bias, and the uncorrected +27.4% was inflated.")
    elif normalised_gap > baseline_gap:
        print("  The two dates already agree on ground that cannot have changed, and")
        print("  normalisation pushes them APART. It is not correcting a bias in the")
        print("  classification - it is destroying real signal at the growth margin.")
    else:
        print("  The two dates already agree on stable ground, so there is no")
        print("  classification bias for normalisation to correct here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
