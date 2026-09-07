"""Test candidate fixes for change-detection speckle against a measured noise floor.

diagnose_stability.py established that comparing two composites of the SAME dry
season - where the true change is zero - still reports ~30 km2 of change over
Bhopal. Any filter can suppress that by suppressing everything, so a filter is
only worth shipping if it removes more noise than signal.

The metric here is the ratio

    churn on the real 7-year run  /  churn on the same-season null test

which is a signal-to-noise ratio. Higher is better. A filter that halves both
numbers has achieved nothing.

Filters compared:
  none        the current pipeline
  focal_mode  3x3 majority vote on each period's built-up mask before
              differencing - standard post-classification smoothing, applied
              identically to both periods so it cannot bias the direction
  mmu5/mmu11  minimum mapping unit: discard change patches below 5 or 11 pixels
              (500 / 1100 m2), applied symmetrically to gain and loss
  focal+mmu5  both

Run from backend/:
    python scripts/evaluate_change_filters.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.amcbi import built_up_mask, compute_amcbi  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import COLLECTION_ID, build_composite  # noqa: E402

AOI_COORDS = [
    [[77.25, 23.15], [77.55, 23.15], [77.55, 23.35], [77.25, 23.35], [77.25, 23.15]]
]

SCALE = 10
CLOUD_THRESHOLD = 20

CASES = {
    "real 7-year run": (("2017-11-01", "2018-02-28"), ("2023-11-01", "2024-02-29")),
    "null: 1 year apart": (("2022-11-01", "2023-02-28"), ("2023-11-01", "2024-02-29")),
    "null: same season": (("2023-11-01", "2023-12-31"), ("2024-01-01", "2024-02-29")),
}


def native_projection(aoi: ee.Geometry) -> ee.Projection:
    """The imagery's own 10 m UTM grid.

    Focal and connectivity operations run in whatever projection the request
    carries, and the default is a 1-degree WGS84 grid. Pinning them to the
    native UTM keeps a '3x3 pixel' kernel actually 30 m across, and avoids the
    web-Mercator distance stretch that matters at this latitude.
    """
    return (
        ee.Image(
            ee.ImageCollection(COLLECTION_ID)
            .filterBounds(aoi)
            .filterDate("2023-11-01", "2024-02-29")
            .first()
        )
        .select("B2")
        .projection()
    )


def classify(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    composite = build_composite(aoi, start, end, CLOUD_THRESHOLD, f"{start}..{end}")
    return built_up_mask(compute_amcbi(composite))


def drop_small_patches(binary: ee.Image, min_pixels: int, proj: ee.Projection) -> ee.Image:
    """Zero out patches of the 1-class smaller than min_pixels."""
    sized = binary.selfMask().reproject(proj).connectedPixelCount(min_pixels + 1, True)
    return binary.And(sized.gte(min_pixels).unmask(0))


def variants(built1: ee.Image, built2: ee.Image, proj: ee.Projection) -> dict:
    """Build (built1, built2, gain, loss) under each candidate filter."""
    out = {}

    def pack(b1: ee.Image, b2: ee.Image, mmu: int = 0) -> tuple:
        common = b1.mask().And(b2.mask())
        b1, b2 = b1.updateMask(common), b2.updateMask(common)
        change = b2.subtract(b1)
        gain, loss = change.eq(1), change.eq(-1)
        if mmu:
            gain = drop_small_patches(gain, mmu, proj)
            loss = drop_small_patches(loss, mmu, proj)
        return b1, b2, gain, loss

    smooth1 = built1.focalMode(1, "square", "pixels").reproject(proj).updateMask(built1.mask())
    smooth2 = built2.focalMode(1, "square", "pixels").reproject(proj).updateMask(built2.mask())

    out["none"] = pack(built1, built2)
    out["focal_mode"] = pack(smooth1, smooth2)
    out["mmu5"] = pack(built1, built2, mmu=5)
    out["mmu11"] = pack(built1, built2, mmu=11)
    out["focal_mmu5"] = pack(smooth1, smooth2, mmu=5)
    return out


def measure(aoi: ee.Geometry, packed: dict) -> dict:
    """One Earth Engine round trip for every filter at once."""
    px = ee.Image.pixelArea()
    stacked = ee.Image.cat(
        [
            band
            for name, (b1, b2, gain, loss) in packed.items()
            for band in (
                px.multiply(b1).rename(f"{name}_b1"),
                px.multiply(b2).rename(f"{name}_b2"),
                px.updateMask(gain.selfMask()).rename(f"{name}_gain"),
                px.updateMask(loss.selfMask()).rename(f"{name}_loss"),
            )
        ]
    )
    raw = stacked.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=SCALE,
        maxPixels=1e10,
        bestEffort=True,
        # 20 bands at 10 m over 680 km2 overruns the per-tile memory budget.
        tileScale=8,
    ).getInfo()
    return {key: (value or 0) / 1e6 for key, value in raw.items()}


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)
    proj = native_projection(aoi)

    results = {}
    for case, ((s1, e1), (s2, e2)) in CASES.items():
        print(f"Measuring {case} ...")
        built1, built2 = classify(aoi, s1, e1), classify(aoi, s2, e2)
        results[case] = measure(aoi, variants(built1, built2, proj))

    filters = ["none", "focal_mode", "mmu5", "mmu11", "focal_mmu5"]

    for case in CASES:
        print(f"\n{case}")
        print(f"  {'filter':<14}{'built p1':>10}{'built p2':>10}{'gain':>9}{'loss':>9}"
              f"{'net %':>9}{'churn %':>9}")
        for name in filters:
            r = results[case]
            b1, b2 = r[f"{name}_b1"], r[f"{name}_b2"]
            gain, loss = r[f"{name}_gain"], r[f"{name}_loss"]
            net_pct = (b2 - b1) / b1 * 100 if b1 else 0
            churn_pct = (gain + loss) / b1 * 100 if b1 else 0
            print(f"  {name:<14}{b1:>10.2f}{b2:>10.2f}{gain:>9.2f}{loss:>9.2f}"
                  f"{net_pct:>+9.1f}{churn_pct:>9.1f}")

    print("\n" + "=" * 68)
    print("SIGNAL TO NOISE: real churn / same-season-null churn (higher is better)")
    print("=" * 68)
    print(f"  {'filter':<14}{'real churn':>13}{'null churn':>13}{'ratio':>9}{'null net %':>12}")
    for name in filters:
        real = results["real 7-year run"]
        null = results["null: same season"]
        real_churn = real[f"{name}_gain"] + real[f"{name}_loss"]
        null_churn = null[f"{name}_gain"] + null[f"{name}_loss"]
        null_net = (
            (null[f"{name}_b2"] - null[f"{name}_b1"]) / null[f"{name}_b1"] * 100
            if null[f"{name}_b1"] else 0
        )
        ratio = real_churn / null_churn if null_churn else float("inf")
        print(f"  {name:<14}{real_churn:>13.2f}{null_churn:>13.2f}{ratio:>9.2f}{null_net:>+12.1f}")

    print("\n  null net % is what the pipeline reports as growth over a span with")
    print("  zero true growth. It is the error bar on the headline number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
