"""Test per-scene voting against the current median-composite classification.

The current pipeline takes a median composite of reflectance and classifies it
once. That is one noisy realisation, and two of its terms make it fragile:

  - k = Blue / SWIR1 is a RATIO, and median(Blue) / median(SWIR1) is not the
    median of k. The composite step does not actually give a robust k.
  - Blue is the band with the largest residual after atmospheric correction, and
    over built-up it is small (~0.10), so its relative error is the worst of the
    four bands feeding AMCBI.

Voting inverts the order: classify every scene on its own, then take the
per-pixel mean of those 0/1 decisions. A pixel is built-up if it was built-up in
most of the scenes that saw it clearly. One hazy scene gets outvoted instead of
dragging a reflectance median, and the vote fraction itself is a per-pixel
confidence - a pixel at 0.5 is genuinely ambiguous, and the current pipeline has
no way to say so.

Run from backend/:
    python scripts/evaluate_voting.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.amcbi import built_up_mask, compute_amcbi  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import (  # noqa: E402
    AMCBI_BANDS,
    COLLECTION_ID,
    REFLECTANCE_SCALE,
    _mask_clouds,
    build_composite,
)

AOI_COORDS = [
    [[77.25, 23.15], [77.55, 23.15], [77.55, 23.35], [77.25, 23.35], [77.25, 23.15]]
]

SCALE = 10
CLOUD_THRESHOLD = 20
VOTE_THRESHOLD = 0.5
MIN_OBSERVATIONS = 3

CASES = {
    "real 7-year run": (("2017-11-01", "2018-02-28"), ("2023-11-01", "2024-02-29")),
    "null: 1 year apart": (("2022-11-01", "2023-02-28"), ("2023-11-01", "2024-02-29")),
    "null: same season": (("2023-11-01", "2023-12-31"), ("2024-01-01", "2024-02-29")),
}


def native_projection(aoi: ee.Geometry) -> ee.Projection:
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


def composite_classify(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    """Current pipeline: median the reflectance, then classify once."""
    composite = build_composite(aoi, start, end, CLOUD_THRESHOLD, f"{start}..{end}")
    return built_up_mask(compute_amcbi(composite))


def vote_fraction(aoi: ee.Geometry, start: str, end: str) -> tuple:
    """Classify every scene, then return (vote fraction, valid observation count)."""
    collection = (
        ee.ImageCollection(COLLECTION_ID)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", CLOUD_THRESHOLD))
        .map(_mask_clouds)
    )
    scenes = collection.map(
        lambda image: built_up_mask(
            compute_amcbi(image.select(AMCBI_BANDS).multiply(REFLECTANCE_SCALE))
        )
    )
    # mean() skips masked pixels, so this is the share of CLEAR looks that came
    # back built-up, not the share of all scenes.
    fraction = scenes.mean().clip(aoi)
    observations = scenes.count().clip(aoi)
    return fraction, observations


def vote_classify(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    fraction, observations = vote_fraction(aoi, start, end)
    # A pixel seen clearly once or twice has no meaningful majority; drop it
    # rather than let a single look decide.
    return fraction.gte(VOTE_THRESHOLD).updateMask(observations.gte(MIN_OBSERVATIONS))


def drop_small_patches(binary: ee.Image, min_pixels: int, proj: ee.Projection) -> ee.Image:
    sized = binary.selfMask().reproject(proj).connectedPixelCount(min_pixels + 1, True)
    return binary.And(sized.gte(min_pixels).unmask(0))


def pack(b1: ee.Image, b2: ee.Image, proj: ee.Projection, mmu: int = 0) -> tuple:
    common = b1.mask().And(b2.mask())
    b1, b2 = b1.updateMask(common), b2.updateMask(common)
    change = b2.subtract(b1)
    gain, loss = change.eq(1), change.eq(-1)
    if mmu:
        gain = drop_small_patches(gain, mmu, proj)
        loss = drop_small_patches(loss, mmu, proj)
    return b1, b2, gain, loss


def measure(aoi: ee.Geometry, packed: dict) -> dict:
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
        reducer=ee.Reducer.sum(), geometry=aoi, scale=SCALE,
        maxPixels=1e10, bestEffort=True, tileScale=8,
    ).getInfo()
    return {key: (value or 0) / 1e6 for key, value in raw.items()}


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)
    proj = native_projection(aoi)

    filters = ["composite", "composite_mmu11", "vote", "vote_mmu11"]
    results = {}

    for case, ((s1, e1), (s2, e2)) in CASES.items():
        print(f"Measuring {case} ...")
        c1, c2 = composite_classify(aoi, s1, e1), composite_classify(aoi, s2, e2)
        v1, v2 = vote_classify(aoi, s1, e1), vote_classify(aoi, s2, e2)
        results[case] = measure(
            aoi,
            {
                "composite": pack(c1, c2, proj),
                "composite_mmu11": pack(c1, c2, proj, mmu=11),
                "vote": pack(v1, v2, proj),
                "vote_mmu11": pack(v1, v2, proj, mmu=11),
            },
        )

    for case in CASES:
        print(f"\n{case}")
        print(f"  {'method':<18}{'built p1':>10}{'built p2':>10}{'gain':>9}{'loss':>9}"
              f"{'net %':>9}{'churn %':>9}")
        for name in filters:
            r = results[case]
            b1, b2 = r[f"{name}_b1"], r[f"{name}_b2"]
            gain, loss = r[f"{name}_gain"], r[f"{name}_loss"]
            net = (b2 - b1) / b1 * 100 if b1 else 0
            churn = (gain + loss) / b1 * 100 if b1 else 0
            print(f"  {name:<18}{b1:>10.2f}{b2:>10.2f}{gain:>9.2f}{loss:>9.2f}"
                  f"{net:>+9.1f}{churn:>9.1f}")

    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    print(f"  {'method':<18}{'real net %':>12}{'null net %':>12}{'null churn %':>14}"
          f"{'real/null':>11}")
    for name in filters:
        real, null = results["real 7-year run"], results["null: same season"]
        real_net = (real[f"{name}_b2"] - real[f"{name}_b1"]) / real[f"{name}_b1"] * 100
        null_net = (null[f"{name}_b2"] - null[f"{name}_b1"]) / null[f"{name}_b1"] * 100
        null_churn = (null[f"{name}_gain"] + null[f"{name}_loss"]) / null[f"{name}_b1"] * 100
        real_churn_km2 = real[f"{name}_gain"] + real[f"{name}_loss"]
        null_churn_km2 = null[f"{name}_gain"] + null[f"{name}_loss"]
        ratio = real_churn_km2 / null_churn_km2 if null_churn_km2 else float("inf")
        print(f"  {name:<18}{real_net:>+12.1f}{null_net:>+12.1f}{null_churn:>14.1f}{ratio:>11.2f}")

    print("\n  null net % should be 0.0 - it measures growth across two halves of one")
    print("  dry season. Whatever it reads is the error bar on real net %.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
