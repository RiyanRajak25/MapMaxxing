"""Run the paper's Bhopal case study end to end and print the statistics.

Reproduces the change analysis in Fig. 7 of Smitha et al. (2026), which reported
roughly 31.8% built-up growth in Bhopal between 2017 and 2024.

Run from the backend/ directory:
    python scripts/run_bhopal.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.change_detection import run_change_detection  # noqa: E402

# Bhopal city and the Bhoj wetland (Upper and Lower Lakes), the area the paper
# studied. Roughly 30 x 22 km.
BHOPAL_AOI = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.25, 23.15],
            [77.55, 23.15],
            [77.55, 23.35],
            [77.25, 23.35],
            [77.25, 23.15],
        ]
    ],
}

# The paper's study period, "2017 and 2024". Each year expands to the
# post-monsoon dry season beginning in it, so both composites sample the same
# phenology. 2017 is the first year Sentinel-2 L2A covers a full dry season.
YEAR_1 = 2017
YEAR_2 = 2024

PAPER_REPORTED_GROWTH_PCT = 31.8


def main() -> int:
    print("Bhopal built-up change, AMCBI over Sentinel-2")
    print(f"  Dry seasons: {YEAR_1} vs {YEAR_2}")
    print("\nQuerying Earth Engine (this takes 20-60 seconds)...\n")

    try:
        result = run_change_detection(
            aoi_geojson=BHOPAL_AOI,
            year1=YEAR_1,
            year2=YEAR_2,
            cloud_threshold=20,
            include_wetland=False,
        )
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1

    stats = result["stats"]
    for label, period in result["periods"].items():
        print(f"  {label}: {period['year']} dry season, {period['start']} to {period['end']}")
    print(f"  Radiometrically normalised: {stats['radiometrically_normalised']}\n")
    print(f"  Area analysed:        {result['aoi_km2']:>10,.1f} km2")
    print(f"  Built-up, period 1:   {stats['built_up_km2_period1']:>10,.2f} km2")
    print(f"  Built-up, period 2:   {stats['built_up_km2_period2']:>10,.2f} km2")
    print(f"  New built-up (gain):  {stats['gain_km2']:>10,.2f} km2")
    print(f"  Lost built-up (loss): {stats['loss_km2']:>10,.2f} km2")
    print(f"  Net change:           {stats['net_change_km2']:>10,.2f} km2")
    print(f"  Net change:           {stats['net_change_pct']:>10} %")

    print(
        f"\n  Unfiltered, gain was {stats['gain_km2_unfiltered']:,.2f} km2 and loss "
        f"{stats['loss_km2_unfiltered']:,.2f} km2."
    )
    print(
        f"  {stats['change_filtered_pct']}% of that was rejected as not credible: either it sat\n"
        f"  within {stats['change_confidence_margin']} of an Eq. 3 bound in one of the periods, or its\n"
        f"  patch was under {stats['min_change_unit_m2']:,.0f} m2."
    )
    print(
        "  Loss is the honest check here: buildings do not disappear, so whatever\n"
        "  loss survives is an estimate of the residual false-change rate."
    )

    observed = stats["net_change_pct"]
    if observed is not None:
        print(f"\n  Paper reported ~{PAPER_REPORTED_GROWTH_PCT}% growth for Bhopal 2017-2024.")
        print(f"  This run measured {observed}%.")
        print(
            "  Exact agreement is not expected: the date windows, cloud filtering and\n"
            "  compositing differ from the paper's single-scene analysis. A result in the\n"
            "  same direction and rough magnitude means the pipeline is behaving."
        )

    print("\n  Map tile layers returned:")
    for name in result["tiles"]:
        print(f"    - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
