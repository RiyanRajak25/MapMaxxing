"""Diagnose why AMCBI classifies so little of Bhopal as built-up.

Decomposes Eq. 3 into its two conditions and reports how many pixels pass each,
plus the actual band reflectances over the dense urban core. If one condition is
rejecting nearly everything, that is the bug.

Run from backend/:
    python scripts/diagnose_amcbi.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.amcbi import K_MAX, K_MIN  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import build_composite  # noqa: E402

AOI_COORDS = [
    [[77.25, 23.15], [77.55, 23.15], [77.55, 23.35], [77.25, 23.35], [77.25, 23.15]]
]

# Dense, unambiguously built-up parts of Bhopal.
URBAN_SAMPLES = {
    "New Market / TT Nagar": [77.4000, 23.2330],
    "Old City / Chowk": [77.4020, 23.2600],
    "Habibganj station area": [77.4340, 23.2330],
    "MP Nagar": [77.4350, 23.2330],
}

PERIOD = {"start": "2023-11-01", "end": "2024-02-29"}


def main() -> int:
    require_earth_engine()
    AOI = ee.Geometry.Polygon(AOI_COORDS)
    print(f"Composite: {PERIOD['start']} to {PERIOD['end']}\n")
    img = build_composite(AOI, PERIOD["start"], PERIOD["end"], 20, "diagnostic")

    blue, red, nir, swir1 = (img.select(b) for b in ("B2", "B4", "B8", "B11"))
    amcbi_star = img.expression(
        "((S + R) - (N + B)) / ((S + R) + (N + B))",
        {"S": swir1, "R": red, "N": nir, "B": blue},
    ).rename("amcbi_star")
    k = blue.divide(swir1).rename("k")

    print("=== Reflectance over known built-up locations ===")
    print(f"{'location':<26}{'blue':>8}{'red':>8}{'nir':>8}{'swir1':>8}{'k':>8}{'amcbi*':>9}")
    for name, coords in URBAN_SAMPLES.items():
        point = ee.Geometry.Point(coords)
        vals = (
            img.addBands(k).addBands(amcbi_star)
            .reduceRegion(ee.Reducer.mean(), point, 20)
            .getInfo()
        )
        if vals.get("B2") is None:
            print(f"{name:<26}   (no data)")
            continue
        passes = "PASS" if (K_MIN <= vals["k"] <= K_MAX and vals["amcbi_star"] > 0) else "FAIL"
        print(
            f"{name:<26}{vals['B2']:>8.3f}{vals['B4']:>8.3f}{vals['B8']:>8.3f}"
            f"{vals['B11']:>8.3f}{vals['k']:>8.3f}{vals['amcbi_star']:>9.3f}  {passes}"
        )

    print("\n=== How many pixels pass each condition (whole AOI) ===")
    cond_star = amcbi_star.gt(0).rename("cond_star")
    cond_k = k.gte(K_MIN).And(k.lte(K_MAX)).rename("cond_k")
    cond_both = cond_star.And(cond_k).rename("cond_both")
    valid = amcbi_star.mask().rename("valid")

    counts = (
        valid.addBands(cond_star).addBands(cond_k).addBands(cond_both)
        .reduceRegion(ee.Reducer.sum(), AOI, 20, maxPixels=1e9, bestEffort=True)
        .getInfo()
    )
    total = counts["valid"] or 1
    for label, key in [
        ("valid pixels", "valid"),
        ("Eq.1  AMCBI* > 0", "cond_star"),
        (f"Eq.2  {K_MIN} <= k <= {K_MAX}", "cond_k"),
        ("both (classified built-up)", "cond_both"),
    ]:
        print(f"  {label:<32}{counts[key]:>12,.0f}  {100 * counts[key] / total:>6.2f}%")

    print("\n=== Distribution of k over the AOI ===")
    pct = (
        k.reduceRegion(
            ee.Reducer.percentile([1, 5, 25, 50, 75, 95, 99]),
            AOI, 20, maxPixels=1e9, bestEffort=True,
        ).getInfo()
    )
    for key in sorted(pct, key=lambda s: int(s.split("p")[1])):
        print(f"  {key:>8}: {pct[key]:.3f}")

    print(
        f"\nIf the k percentiles sit mostly below {K_MIN}, the constraint calibrated in the\n"
        "paper does not transfer to this imagery, and k is the bottleneck - not Eq. 1."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
