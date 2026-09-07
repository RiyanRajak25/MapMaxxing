"""Measure how much of the detected change is real and how much is noise.

Two questions, one answer.

  1. HOW ACCURATE IS THE CHANGE DETECTION? There is no reference data yet, but a
     null test needs none: compare two composites over a span in which the true
     built-up change is zero by construction. Every square kilometre it reports
     is error. That is a hard, reference-free noise floor.

  2. WHY DOES A GROWING CITY LOSE BUILT-UP AREA? Buildings do not disappear.
     Measured loss over Bhopal is therefore mostly false change, which makes it
     a second, independent estimate of the same error rate.

The script then locates the cause. Built-up requires AMCBI* > 0 AND
K_MIN <= k <= K_MAX. Both are hard thresholds applied to two independently noisy
composites, so any pixel sitting near either boundary flips on measurement noise
alone. Test C measures how close the flipping pixels actually sit to the
boundary, and Test D decomposes which of the two conditions let go.

Run from backend/:
    python scripts/diagnose_stability.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.amcbi import K_MAX, K_MIN, built_up_mask, compute_amcbi  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import COLLECTION_ID, build_composite  # noqa: E402

AOI_COORDS = [
    [[77.25, 23.15], [77.55, 23.15], [77.55, 23.35], [77.25, 23.35], [77.25, 23.15]]
]

SCALE = 10
CLOUD_THRESHOLD = 20

# The real analysis, for reference.
REAL = (("2017-11-01", "2018-02-28"), ("2023-11-01", "2024-02-29"))

# Null test 1: consecutive years, same season, same composite density as the real
# run. Seven years of growth compressed into one means true gain is small and
# true loss is still zero.
YEAR_APART = (("2022-11-01", "2023-02-28"), ("2023-11-01", "2024-02-29"))

# Null test 2: one dry season split in half. Nothing is built and nothing is
# demolished across this gap, so the true change is zero outright. Each composite
# holds fewer scenes than the real run, so this overstates the floor a little -
# it is the pessimistic bound.
SEASON_SPLIT = (("2023-11-01", "2023-12-31"), ("2024-01-01", "2024-02-29"))

# A loss patch smaller than this is not a demolished building, it is speckle.
# 5 pixels at 10 m is 500 m2.
SPECKLE_PIXELS = 5


def scene_count(aoi: ee.Geometry, start: str, end: str) -> int:
    return int(
        ee.ImageCollection(COLLECTION_ID)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", CLOUD_THRESHOLD))
        .size()
        .getInfo()
    )


def decompose(aoi: ee.Geometry, start: str, end: str):
    """Return the two Eq. 3 terms and the built-up mask for one period."""
    img = build_composite(aoi, start, end, CLOUD_THRESHOLD, f"{start}..{end}")
    blue, red, nir, swir1 = (img.select(b) for b in ("B2", "B4", "B8", "B11"))
    star = img.expression(
        "((S + R) - (N + B)) / ((S + R) + (N + B))",
        {"S": swir1, "R": red, "N": nir, "B": blue},
    ).rename("star")
    k = blue.divide(swir1).rename("k")
    return star, k, built_up_mask(compute_amcbi(img))


def change_areas(aoi: ee.Geometry, built1: ee.Image, built2: ee.Image) -> dict:
    """Built-up area per period plus gain and loss, in km2."""
    common = built1.mask().And(built2.mask())
    b1, b2 = built1.updateMask(common), built2.updateMask(common)
    change = b2.subtract(b1)
    gain, loss = change.eq(1), change.eq(-1)

    px = ee.Image.pixelArea()
    stacked = (
        px.multiply(b1).rename("b1")
        .addBands(px.multiply(b2).rename("b2"))
        .addBands(px.updateMask(gain.selfMask()).rename("gain"))
        .addBands(px.updateMask(loss.selfMask()).rename("loss"))
    )
    r = stacked.reduceRegion(
        ee.Reducer.sum(), aoi, SCALE, maxPixels=1e9, bestEffort=True, tileScale=4
    ).getInfo()

    return {name: (r.get(name) or 0) / 1e6 for name in ("b1", "b2", "gain", "loss")}


def report(label: str, aoi: ee.Geometry, periods) -> dict:
    (s1, e1), (s2, e2) = periods
    n1, n2 = scene_count(aoi, s1, e1), scene_count(aoi, s2, e2)
    _, _, built1 = decompose(aoi, s1, e1)
    _, _, built2 = decompose(aoi, s2, e2)
    a = change_areas(aoi, built1, built2)

    churn = (a["gain"] + a["loss"]) / a["b1"] * 100 if a["b1"] else 0
    loss_pct = a["loss"] / a["b1"] * 100 if a["b1"] else 0
    net_pct = (a["b2"] - a["b1"]) / a["b1"] * 100 if a["b1"] else 0

    print(f"\n{label}")
    print(f"  {s1}..{e1} ({n1} scenes)  vs  {s2}..{e2} ({n2} scenes)")
    print(f"    built-up p1 {a['b1']:8.2f} km2      built-up p2 {a['b2']:8.2f} km2")
    print(f"    gain        {a['gain']:8.2f} km2      loss        {a['loss']:8.2f} km2")
    print(f"    net change  {net_pct:+7.1f} %       loss / p1   {loss_pct:7.1f} %")
    print(f"    churn (gain+loss) / p1                          {churn:7.1f} %")
    return a


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)

    print("=" * 72)
    print("A. IS THE MEASURED CHANGE REAL?  Null tests over spans with no true change")
    print("=" * 72)

    real = report("REAL RUN (7 years apart - the number we report)", aoi, REAL)
    year = report("NULL TEST 1 (1 year apart, full composites)", aoi, YEAR_APART)
    split = report("NULL TEST 2 (same dry season, split in half)", aoi, SEASON_SPLIT)

    print("\n" + "-" * 72)
    floor = split["gain"] + split["loss"]
    real_churn = real["gain"] + real["loss"]
    print(f"  Noise floor from the same-season split: {floor:.2f} km2 of change")
    print(f"  reported where the true change is zero. That is {floor / real_churn * 100:.0f}% of")
    print(f"  the {real_churn:.2f} km2 of change the real run reports.")

    print("\n" + "=" * 72)
    print("B. WHY DOES A CITY LOSE BUILT-UP AREA?  Where the lost pixels sit")
    print("=" * 72)

    star1, k1, built1 = decompose(aoi, *REAL[0])
    star2, k2, built2 = decompose(aoi, *REAL[1])
    common = built1.mask().And(built2.mask())
    b1, b2 = built1.updateMask(common), built2.updateMask(common)

    lost = b1.And(b2.Not())
    stable = b1.And(b2)

    print("\nC. Distance from the decision boundary in period 1")
    print("   If the pixels that vanish were sitting right on the threshold, the")
    print("   loss is threshold churn, not demolition.\n")

    margin_k = k1.subtract(K_MIN).rename("margin_k")
    margin_star = star1.rename("margin_star")
    margins = margin_k.addBands(margin_star)

    print(f"   {'pixel class':<22}{'median k margin':>18}{'median AMCBI*':>16}")
    for name, mask in (("stayed built-up", stable), ("lost built-up", lost)):
        vals = margins.updateMask(mask).reduceRegion(
            ee.Reducer.median(), aoi, SCALE, maxPixels=1e9, bestEffort=True, tileScale=4
        ).getInfo()
        mk, ms = vals.get("margin_k"), vals.get("margin_star")
        if mk is None:
            print(f"   {name:<22}{'(no pixels)':>18}")
            continue
        print(f"   {name:<22}{mk:>18.4f}{ms:>16.4f}")

    print("\nD. Which condition let go in period 2, for the pixels that were lost")
    star_failed = star2.lte(0)
    k_failed = k2.lt(K_MIN).Or(k2.gt(K_MAX))
    breakdown = (
        star_failed.And(k_failed.Not()).rename("star_only")
        .addBands(k_failed.And(star_failed.Not()).rename("k_only"))
        .addBands(star_failed.And(k_failed).rename("both"))
        .updateMask(lost)
    )
    counts = breakdown.reduceRegion(
        ee.Reducer.sum(), aoi, SCALE, maxPixels=1e9, bestEffort=True, tileScale=4
    ).getInfo()
    total = sum(counts.get(key) or 0 for key in ("star_only", "k_only", "both")) or 1
    for label, key in (
        ("AMCBI* dropped to <= 0", "star_only"),
        (f"k left [{K_MIN}, {K_MAX}]", "k_only"),
        ("both failed", "both"),
    ):
        n = counts.get(key) or 0
        print(f"   {label:<32}{n:>12,.0f} px{100 * n / total:>8.1f} %")

    print("\nE. Are the lost pixels isolated speckle or real patches?")
    patch = lost.selfMask().connectedPixelCount(SPECKLE_PIXELS + 1, True)
    px = ee.Image.pixelArea()
    speckle = (
        px.updateMask(lost.And(patch.lte(SPECKLE_PIXELS))).rename("speckle")
        .addBands(px.updateMask(lost).rename("all_loss"))
        .reduceRegion(ee.Reducer.sum(), aoi, SCALE, maxPixels=1e9, bestEffort=True, tileScale=4)
        .getInfo()
    )
    all_loss = (speckle.get("all_loss") or 0) / 1e6
    spec = (speckle.get("speckle") or 0) / 1e6
    print(f"   loss in patches <= {SPECKLE_PIXELS} px: {spec:.2f} km2 of {all_loss:.2f} km2"
          f"  ({100 * spec / all_loss if all_loss else 0:.0f}%)")
    print("   Scattered single pixels are noise. Coherent patches could be real")
    print("   (a filled quarry, a flooded margin) and deserve a look on the map.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
