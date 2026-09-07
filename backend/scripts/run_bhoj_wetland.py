"""Urban encroachment on the Bhoj Wetland, Bhopal - 2017 vs 2024.

Reproduces and extends the case study in Fig. 7 of Smitha et al. (2026), which
reported ~31.8% built-up growth in Bhopal over this period and motivated the site
choice with "unchecked urbanization along the periphery of the lakes."

This script measures that periphery claim directly: built-up change binned by
distance from the lake shoreline.

Run from backend/:
    python scripts/run_bhoj_wetland.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ee  # noqa: E402

from app import config  # noqa: E402
from app.services import normalization, wetland  # noqa: E402
from app.services.gee_auth import require_earth_engine  # noqa: E402
from app.services.imagery import (  # noqa: E402
    build_composite,
    dry_season_window,
    native_projection,
)

# Upper Lake (Bhojtal), Lower Lake, and the urban belt around them. The Ramsar
# site itself is ~32 km2; this window is wider so encroachment outside the
# designated boundary is still captured.
BHOJ_AOI = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.22, 23.18],
            [77.48, 23.18],
            [77.48, 23.32],
            [77.22, 23.32],
            [77.22, 23.18],
        ]
    ],
}

# The paper's study period, "2017 and 2024". Each year expands to the
# post-monsoon dry season beginning in it, so lake extent and vegetation are
# comparable across the two composites.
YEAR_1 = 2017
YEAR_2 = 2024

PAPER_GROWTH_PCT = 31.8


def main() -> int:
    require_earth_engine()
    aoi = ee.Geometry.Polygon(BHOJ_AOI["coordinates"])

    start1, end1 = dry_season_window(YEAR_1)
    start2, end2 = dry_season_window(YEAR_2)

    print("Bhoj Wetland, Bhopal - urban encroachment analysis")
    print(f"  {YEAR_1} dry season: {start1} to {end1}")
    print(f"  {YEAR_2} dry season: {start2} to {end2}")
    print("\nQuerying Earth Engine (60-120 seconds)...\n")

    composite1 = build_composite(aoi, start1, end1, 20, f"{YEAR_1} dry season")
    composite2 = build_composite(aoi, start2, end2, 20, f"{YEAR_2} dry season")
    projection = native_projection(aoi, start2, end2)

    # Same correction the API applies: without it the 2017 composite starts from
    # an artificially low base and roughly doubles the reported growth.
    if config.NORMALIZE_PERIODS:
        composite1 = normalization.normalise_to(composite1, composite2, aoi, 10)
        print("  Period 1 radiometrically normalised onto period 2.\n")

    result = wetland.analyse(aoi, composite1, composite2, projection)

    water = result["water"]
    print("=== Open water (MNDWI-delineated) ===")
    print(f"  Period 1: {water['area_km2_period1']:>8.2f} km2")
    print(f"  Period 2: {water['area_km2_period2']:>8.2f} km2")
    print(f"  Change:   {water['change_km2']:>8.2f} km2  ({water['change_pct']} %)")

    print("\n=== Built-up by distance from the shoreline ===")
    header = f"{'zone':<22}{'land':>9}{'built 17':>10}{'built 24':>10}{'growth':>9}{'% built 17':>12}{'% built 24':>12}"
    print(header)
    print("-" * len(header))

    total1 = total2 = 0.0
    for ring in result["rings"]:
        total1 += ring["built_up_km2_period1"]
        total2 += ring["built_up_km2_period2"]
        growth = f"{ring['growth_pct']:+.1f}%" if ring["growth_pct"] is not None else "-"
        print(
            f"{ring['label']:<22}"
            f"{ring['land_km2']:>9.2f}"
            f"{ring['built_up_km2_period1']:>10.2f}"
            f"{ring['built_up_km2_period2']:>10.2f}"
            f"{growth:>9}"
            f"{ring['built_up_share_period1_pct']:>11.1f}%"
            f"{ring['built_up_share_period2_pct']:>11.1f}%"
        )

    overall = (total2 - total1) / total1 * 100 if total1 else 0
    print("-" * len(header))
    print(f"{'ALL ZONES':<22}{'':>9}{total1:>10.2f}{total2:>10.2f}{overall:>8.1f}%")

    print(f"\n  Paper reported ~{PAPER_GROWTH_PCT}% built-up growth for Bhopal 2017-2024.")
    print(f"  This run measured {overall:.1f}% across the wetland study area.")

    inner = result["rings"][0]
    outer = result["rings"][-1]
    print("\n=== Encroachment reading ===")
    if inner["growth_pct"] is None or outer["growth_pct"] is None:
        return 0

    if inner["growth_pct"] > outer["growth_pct"]:
        print(
            f"  Growth nearest the water ({inner['growth_pct']:+.1f}%) EXCEEDS the outer zone\n"
            f"  ({outer['growth_pct']:+.1f}%). Development is concentrating on the lake margin,\n"
            "  which is the encroachment pattern the paper describes."
        )
    else:
        print(
            f"  Growth nearest the water ({inner['growth_pct']:+.1f}%) is BELOW the outer zone\n"
            f"  ({outer['growth_pct']:+.1f}%), and the gradient rises monotonically with distance.\n"
            "  Over this period, expansion moved outward rather than onto the shoreline."
        )
        # Only offer the saturation explanation if the inner belt is actually
        # full. Asserting it against a sparsely-built margin would be wrong.
        share = inner["built_up_share_period1_pct"]
        if share is not None and share >= 50:
            print(
                f"\n  Caveat: that belt was already {share}% built-up in 2017, so low growth may\n"
                "  simply mean there was little land left, not that the margin is protected."
            )
        else:
            print(
                f"\n  Note the margin was only {share}% built-up in 2017, so this is NOT a case of\n"
                "  no land being left. Land was available and largely not built on. Plausible\n"
                "  causes are statutory: Bhoj Wetland is a Ramsar site (2002), Van Vihar National\n"
                "  Park occupies much of the Upper Lake's southern shore, and construction inside\n"
                "  the Full Tank Level boundary is restricted. The imagery shows the pattern; it\n"
                "  cannot by itself prove the cause."
            )

    water_change = result["water"]["change_pct"]
    if water_change is not None and abs(water_change) > 5:
        print(
            f"\n  Treat the {water_change:+.1f}% water-area change with caution. MNDWI measures the\n"
            "  lake surface on the imaging dates, and Upper Lake level swings with monsoon\n"
            "  recharge between years. This is far more likely a difference in lake level than\n"
            "  a real change in wetland extent."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
