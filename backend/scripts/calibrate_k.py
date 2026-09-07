"""Calibrate the AMCBI k threshold for Sentinel-2 L2A surface reflectance.

The paper's k >= 0.6 was calibrated against reflectances (Fig. 2a: built-up blue
~0.21) roughly twice what Sentinel-2 L2A actually reports over built-up areas
(~0.10). Applied to L2A, k >= 0.6 rejects almost all genuine built-up.

k's job is to separate built-up from bare soil and rock, which Eq. 1 alone cannot
do. This script measures k for each cover type on real L2A imagery and reports the
value that best separates built-up from bare soil, using the same midpoint-of-
distributions logic the paper's Fig. 2b implies.

Run from backend/:
    python scripts/calibrate_k.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import build_composite  # noqa: E402

AOI_COORDS = [
    [[77.20, 23.10], [77.60, 23.10], [77.60, 23.40], [77.20, 23.40], [77.20, 23.10]]
]
PERIOD = {"start": "2023-11-01", "end": "2024-02-29"}

# Cover-type reference points around Bhopal, chosen from the dense urban core,
# the Bhoj wetland, and the agricultural belt outside the city.
SAMPLES = {
    "built_up": [
        [77.4000, 23.2330],  # New Market / TT Nagar
        [77.4020, 23.2600],  # Old City / Chowk
        [77.4340, 23.2330],  # Habibganj
        [77.4350, 23.2530],  # MP Nagar
        [77.3760, 23.2270],  # Shyamla Hills
        [77.4600, 23.2200],  # Bagsewaniya
    ],
    "water": [
        [77.3400, 23.2470],  # Upper Lake
        [77.3250, 23.2550],
        [77.4150, 23.2650],  # Lower Lake
    ],
    "vegetation": [
        [77.3550, 23.2100],  # Van Vihar forest
        [77.3450, 23.2050],
        [77.5200, 23.3300],
    ],
    "bare_or_crop": [
        [77.2600, 23.3600],
        [77.5500, 23.3600],
        [77.5600, 23.1400],
        [77.2500, 23.1300],
    ],
}


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)
    img = build_composite(aoi, PERIOD["start"], PERIOD["end"], 20, "calibration")

    blue, red, nir, swir1 = (img.select(b) for b in ("B2", "B4", "B8", "B11"))
    k = blue.divide(swir1).rename("k")
    amcbi_star = img.expression(
        "((S + R) - (N + B)) / ((S + R) + (N + B))",
        {"S": swir1, "R": red, "N": nir, "B": blue},
    ).rename("amcbi_star")
    ndvi = nir.subtract(red).divide(nir.add(red)).rename("ndvi")
    stack = img.addBands(k).addBands(amcbi_star).addBands(ndvi)

    print(f"Sentinel-2 L2A composite {PERIOD['start']} to {PERIOD['end']}\n")
    print(f"{'cover':<14}{'blue':>7}{'swir1':>7}{'k':>8}{'amcbi*':>9}{'ndvi':>7}")

    measured = {}
    for cover, points in SAMPLES.items():
        ks, stars = [], []
        for coords in points:
            vals = (
                stack.reduceRegion(ee.Reducer.mean(), ee.Geometry.Point(coords).buffer(30), 20)
                .getInfo()
            )
            if vals.get("k") is None:
                continue
            ks.append(vals["k"])
            stars.append(vals["amcbi_star"])
            print(
                f"{cover:<14}{vals['B2']:>7.3f}{vals['B11']:>7.3f}{vals['k']:>8.3f}"
                f"{vals['amcbi_star']:>9.3f}{vals['ndvi']:>7.3f}"
            )
        if ks:
            measured[cover] = {"k": ks, "amcbi_star": stars}

    print("\n=== k by cover type ===")
    for cover, data in measured.items():
        ks = data["k"]
        print(f"  {cover:<14} n={len(ks)}  min={min(ks):.3f}  mean={sum(ks)/len(ks):.3f}  max={max(ks):.3f}")

    built = measured.get("built_up", {}).get("k", [])
    bare = measured.get("bare_or_crop", {}).get("k", [])
    if built and bare:
        print("\n=== Separating built-up from bare/crop ===")
        print(f"  lowest built-up k:      {min(built):.3f}")
        print(f"  highest bare/crop k:    {max(bare):.3f}")
        if min(built) > max(bare):
            suggested = round((min(built) + max(bare)) / 2, 2)
            print(f"  classes are separable; midpoint K_MIN = {suggested}")
        else:
            suggested = round(min(built) - 0.02, 2)
            print(f"  classes OVERLAP - no clean split. Floor below built-up: K_MIN = {suggested}")
        print(f"\n  Paper's value: 0.60   Suggested for L2A: {suggested}")

    print("\n=== Upper bound ===")
    water = measured.get("water", {}).get("k", [])
    if water:
        print(f"  water k: min={min(water):.3f} max={max(water):.3f}")
        print("  K_MAX must stay below water's k so lakes are not read as built-up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
