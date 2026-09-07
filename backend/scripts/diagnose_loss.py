"""Establish what the surviving "lost built-up" pixels actually are.

The claim under test is physical, not statistical: buildings do not disappear.
Over Bhopal 2017-2024 there is no demolition programme, so TRUE loss is close to
zero and every square metre the pipeline reports as loss is a commission error.
That makes loss a free, reference-free measure of classifier error - and it also
means the loss layer as shipped is mostly wrong.

PART 1 asks where the lost pixels landed in period 2. If they still satisfy
Eq. 1 (they still look built-up spectrally) and their k sits just under K_MIN,
the building is plainly still there and the pixel was lost to threshold noise.

PART 2 checks whether known dense-urban locations fall inside the loss class.
Any hit there is unambiguous: those addresses did not vanish.

PART 3 evaluates a hysteresis rule. Change is currently declared by comparing
two independent hard thresholds, which double-counts the noise of both periods.
Hysteresis instead demands DECISIVE evidence at both ends - clearly non-built
before and clearly built after for gain, and the reverse for loss - so a pixel
hovering at the boundary produces no change call at all rather than a coin flip.
It is applied symmetrically, so it cannot manufacture growth.

Run from backend/:
    python scripts/diagnose_loss.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app.services.amcbi import K_MAX, K_MIN, built_up_mask, compute_amcbi  # noqa: E402
from app.services.change_filters import CHANGE_MIN_PIXELS, drop_small_patches  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import build_composite, native_projection  # noqa: E402

AOI_COORDS = [
    [[77.25, 23.15], [77.55, 23.15], [77.55, 23.35], [77.25, 23.35], [77.25, 23.15]]
]

SCALE = 10
CLOUD_THRESHOLD = 20

REAL = (("2017-11-01", "2018-02-28"), ("2023-11-01", "2024-02-29"))
NULL = (("2023-11-01", "2023-12-31"), ("2024-01-01", "2024-02-29"))

# Dense, unambiguously built-up since well before 2017. None of these were
# demolished between the two periods.
URBAN_SAMPLES = {
    "New Market / TT Nagar": [77.4000, 23.2330],
    "Old City / Chowk": [77.4020, 23.2600],
    "Habibganj station area": [77.4340, 23.2330],
    "MP Nagar": [77.4350, 23.2330],
}

DELTAS = [0.0, 0.02, 0.04]


def terms(aoi: ee.Geometry, start: str, end: str) -> tuple:
    """Eq. 1 and Eq. 2 terms plus the shipped built-up mask for one period."""
    img = build_composite(aoi, start, end, CLOUD_THRESHOLD, f"{start}..{end}")
    blue, red, nir, swir1 = (img.select(b) for b in ("B2", "B4", "B8", "B11"))
    star = img.expression(
        "((S + R) - (N + B)) / ((S + R) + (N + B))",
        {"S": swir1, "R": red, "N": nir, "B": blue},
    ).rename("star")
    k = blue.divide(swir1).rename("k")
    return star, k, built_up_mask(compute_amcbi(img))


def core_built(star: ee.Image, k: ee.Image, delta: float) -> ee.Image:
    """Built-up with margin to spare - decisive evidence."""
    return star.gt(delta).And(k.gte(K_MIN + delta)).And(k.lte(K_MAX))


def loose_built(star: ee.Image, k: ee.Image, delta: float) -> ee.Image:
    """Built-up under a generous reading. Its NEGATION is decisive non-built-up.

    K_MAX is deliberately not relaxed: above it lies water, and letting the lake
    edge in would be a far worse error than the one being fixed.
    """
    return star.gt(-delta).And(k.gte(K_MIN - delta)).And(k.lte(K_MAX))


def hysteresis_change(
    star1, k1, built1, star2, k2, built2, delta: float, projection
) -> tuple:
    """Gain and loss requiring decisive evidence at both ends."""
    common = built1.mask().And(built2.mask())
    if delta == 0:
        # Degenerate case: identical to the current pipeline.
        b1, b2 = built1.updateMask(common), built2.updateMask(common)
        gain, loss = b2.And(b1.Not()), b1.And(b2.Not())
    else:
        core1 = core_built(star1, k1, delta).updateMask(common)
        core2 = core_built(star2, k2, delta).updateMask(common)
        loose1 = loose_built(star1, k1, delta).updateMask(common)
        loose2 = loose_built(star2, k2, delta).updateMask(common)
        gain = core2.And(loose1.Not())
        loss = core1.And(loose2.Not())
    return drop_small_patches(gain, projection), drop_small_patches(loss, projection)


def area_km2(image: ee.Image, aoi: ee.Geometry) -> ee.Image:
    return ee.Image.pixelArea().updateMask(image.selfMask())


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(AOI_COORDS)
    projection = native_projection(aoi, *REAL[0])

    star1, k1, built1 = terms(aoi, *REAL[0])
    star2, k2, built2 = terms(aoi, *REAL[1])
    common = built1.mask().And(built2.mask())
    b1, b2 = built1.updateMask(common), built2.updateMask(common)

    raw_loss = b1.And(b2.Not())
    kept_loss = drop_small_patches(raw_loss, projection)

    print("=" * 74)
    print("PART 1  What happened to the pixels reported as LOST built-up?")
    print("=" * 74)
    print("        (measured on loss that SURVIVES the current speckle filter)\n")

    # Does the pixel still look built-up in period 2 under Eq. 1 alone, and how
    # far below K_MIN did k actually fall?
    probes = (
        star2.gt(0).rename("still_eq1")
        .addBands(k2.gte(K_MIN - 0.02).rename("k_within_002"))
        .addBands(k2.gte(K_MIN - 0.04).rename("k_within_004"))
        .addBands(star2.gt(0).And(k2.gte(K_MIN - 0.04)).And(k2.lte(K_MAX)).rename("nearly_passes"))
        .addBands(ee.Image(1).rename("total"))
        .updateMask(kept_loss.selfMask())
    )
    counts = probes.reduceRegion(
        ee.Reducer.sum(), aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8
    ).getInfo()
    total = counts.get("total") or 1

    for label, key in (
        ("still satisfy Eq. 1 (look built-up)", "still_eq1"),
        ("k within 0.02 of K_MIN", "k_within_002"),
        ("k within 0.04 of K_MIN", "k_within_004"),
        ("would pass with K_MIN lowered 0.04", "nearly_passes"),
    ):
        n = counts.get(key) or 0
        print(f"  {label:<40}{n:>12,.0f} px{100 * n / total:>8.1f} %")

    stats = (
        k2.rename("k2").addBands(star2.rename("star2"))
        .updateMask(kept_loss.selfMask())
        .reduceRegion(
            ee.Reducer.percentile([10, 50, 90]), aoi, SCALE,
            maxPixels=1e10, bestEffort=True, tileScale=8,
        ).getInfo()
    )
    print(f"\n  Period-2 k over lost pixels   p10 {stats.get('k2_p10'):.3f}"
          f"   median {stats.get('k2_p50'):.3f}   p90 {stats.get('k2_p90'):.3f}"
          f"   (K_MIN = {K_MIN})")
    print(f"  Period-2 AMCBI* over the same  p10 {stats.get('star2_p10'):.3f}"
          f"   median {stats.get('star2_p50'):.3f}   p90 {stats.get('star2_p90'):.3f}")

    print("\n" + "=" * 74)
    print("PART 2  Do known dense-urban locations fall in the loss class?")
    print("=" * 74)
    print("        These addresses were built long before 2017 and still stand.\n")

    probe = (
        b1.rename("built_2017")
        .addBands(b2.rename("built_2024"))
        .addBands(kept_loss.rename("reported_lost"))
        .addBands(k2.rename("k_2024"))
        .addBands(star2.rename("star_2024"))
    )
    print(f"  {'location':<26}{'2017':>7}{'2024':>7}{'LOST':>7}{'k 2024':>9}{'AMCBI* 24':>11}")
    for name, coords in URBAN_SAMPLES.items():
        vals = probe.reduceRegion(
            ee.Reducer.mean(), ee.Geometry.Point(coords).buffer(30), SCALE
        ).getInfo()
        if vals.get("built_2017") is None:
            print(f"  {name:<26}  (no data)")
            continue
        flag = "YES" if (vals["reported_lost"] or 0) > 0.5 else "-"
        print(f"  {name:<26}{vals['built_2017']:>7.2f}{vals['built_2024']:>7.2f}"
              f"{flag:>7}{vals['k_2024']:>9.3f}{vals['star_2024']:>11.3f}")

    print("\n" + "=" * 74)
    print("PART 3  Hysteresis: demand decisive evidence before calling change")
    print("=" * 74)
    print("        True loss over Bhopal is ~0, so the real-run loss column is")
    print("        almost entirely error. The null columns should also be ~0.\n")

    nstar1, nk1, nbuilt1 = terms(aoi, *NULL[0])
    nstar2, nk2, nbuilt2 = terms(aoi, *NULL[1])

    bands = []
    for delta in DELTAS:
        rg, rl = hysteresis_change(star1, k1, built1, star2, k2, built2, delta, projection)
        ng, nl = hysteresis_change(nstar1, nk1, nbuilt1, nstar2, nk2, nbuilt2, delta, projection)
        tag = f"d{int(delta * 100):02d}"
        bands += [
            area_km2(rg, aoi).rename(f"{tag}_real_gain"),
            area_km2(rl, aoi).rename(f"{tag}_real_loss"),
            area_km2(ng, aoi).rename(f"{tag}_null_gain"),
            area_km2(nl, aoi).rename(f"{tag}_null_loss"),
        ]

    totals = ee.Image.cat(bands).reduceRegion(
        ee.Reducer.sum(), aoi, SCALE, maxPixels=1e10, bestEffort=True, tileScale=8
    ).getInfo()

    print(f"  {'margin':<10}{'real gain':>11}{'real loss':>11}{'null gain':>11}"
          f"{'null loss':>11}{'gain/error':>12}")
    for delta in DELTAS:
        tag = f"d{int(delta * 100):02d}"
        rg = (totals.get(f"{tag}_real_gain") or 0) / 1e6
        rl = (totals.get(f"{tag}_real_loss") or 0) / 1e6
        ng = (totals.get(f"{tag}_null_gain") or 0) / 1e6
        nl = (totals.get(f"{tag}_null_loss") or 0) / 1e6
        error = rl + ng + nl
        ratio = rg / error if error else float("inf")
        label = "none" if delta == 0 else f"{delta:.2f}"
        print(f"  {label:<10}{rg:>11.2f}{rl:>11.2f}{ng:>11.2f}{nl:>11.2f}{ratio:>12.2f}")

    print(f"\n  All rows already include the {CHANGE_MIN_PIXELS} px minimum mapping unit.")
    print("  'gain/error' is real gain divided by everything we know to be wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
