"""Wetland encroachment analysis.

The source paper selects Bhopal because "unchecked urbanization along the
periphery of the lakes" degrades the Bhoj Wetland. A single built-up percentage
for the whole city cannot show that. What shows it is built-up change measured as
a function of distance from the water: if growth concentrates in the inner rings,
the wetland is being encroached on rather than the city simply expanding outward.

Water is delineated from the imagery itself with MNDWI rather than a fixed
polygon, so the lake boundary reflects each period's actual extent.
"""

import ee

from app.services import amcbi as amcbi_service
from app.services import change_filters

# MNDWI above this is water. 0.1 rather than 0 keeps damp soil and shadowed
# vegetation out of the lake mask.
MNDWI_WATER_THRESHOLD = 0.1

# A lake must cover at least this many pixels to count, which drops ponds,
# rooftop tanks and speckle from the distance transform.
MIN_WATER_PATCH_PIXELS = 200

# Distance bands from the shoreline, in metres. The last ring is open-ended.
RING_EDGES_M = [500, 1000, 2000]

# The distance transform runs at a coarser scale than the imagery. Two reasons:
# ring assignment does not need 10 m precision, and Earth Engine caps convolution
# kernels at 512 pixels across - at 10 m native, a 2500 m radius alone would need
# 501 px, leaving no headroom. At 25 m the same radius is 101 px across.
DISTANCE_SCALE = 25
MAX_DISTANCE_M = 2500


def water_mask(image: ee.Image) -> ee.Image:
    """Delineate open water using MNDWI = (Green - SWIR1) / (Green + SWIR1)."""
    mndwi = image.normalizedDifference(["B3", "B11"]).rename("mndwi")
    water = mndwi.gt(MNDWI_WATER_THRESHOLD)
    # connectedPixelCount is evaluated on the water class only, so small isolated
    # patches can be dropped without touching large lakes.
    patch_size = water.selfMask().connectedPixelCount(MIN_WATER_PATCH_PIXELS, False)
    return water.And(patch_size.gte(MIN_WATER_PATCH_PIXELS)).unmask(0).rename("water")


def ring_labels(water: ee.Image) -> ee.Image:
    """Label every land pixel with its distance ring from the shoreline.

    Ring 1 is nearest the water. Water pixels themselves are excluded, so lake
    surface never counts as land in any ring.
    """
    # Reproject to the coarser analysis scale first, in the image's own metric
    # UTM projection, so the kernel stays inside Earth Engine's 512 px limit and
    # distances stay true (a web-Mercator reprojection would stretch them by
    # ~9% at this latitude).
    coarse = water.projection().atScale(DISTANCE_SCALE)
    distance = (
        water.selfMask()
        .reproject(coarse)
        .distance(ee.Kernel.euclidean(MAX_DISTANCE_M, "meters"), False)
        .rename("distance")
        # Beyond the kernel radius the result is masked; those pixels are simply
        # the farthest ring.
        .unmask(MAX_DISTANCE_M + 1)
    )

    label = ee.Image(len(RING_EDGES_M) + 1)
    for index, edge in reversed(list(enumerate(RING_EDGES_M))):
        label = label.where(distance.lte(edge), index + 1)

    return label.updateMask(water.Not()).rename("ring").toInt()


def ring_definitions() -> list[dict]:
    """Human-readable descriptions of each ring, in order."""
    rings = []
    previous = 0
    for index, edge in enumerate(RING_EDGES_M):
        rings.append({"index": index + 1, "label": f"{previous}-{edge} m from water"})
        previous = edge
    rings.append({"index": len(RING_EDGES_M) + 1, "label": f"beyond {previous} m"})
    return rings


def analyse(
    aoi: ee.Geometry,
    composite1: ee.Image,
    composite2: ee.Image,
    projection: ee.Projection,
    scale: int = 10,
) -> dict:
    """Measure built-up change per distance ring, plus lake area change."""
    water1 = water_mask(composite1)
    water2 = water_mask(composite2)

    # Rings are fixed to the earlier shoreline so both periods are measured
    # against the same geography. Using each period's own shoreline would move
    # the rings underneath the comparison.
    rings = ring_labels(water1)

    star1, k1 = amcbi_service.constraint_terms(composite1)
    star2, k2 = amcbi_service.constraint_terms(composite2)
    built1 = amcbi_service.built_up_with_margin(star1, k1, 0)
    built2 = amcbi_service.built_up_with_margin(star2, k2, 0)
    common = built1.mask().And(built2.mask())
    built1 = built1.updateMask(common)
    built2 = built2.updateMask(common)

    # Same hysteresis and minimum mapping unit as the main analysis, or the
    # per-ring gradient would be measured on unfiltered churn while the headline
    # number was not. The ring GROWTH percentages come from built1/built2 and are
    # untouched by either filter; only the gain and loss columns are affected.
    gain, loss = change_filters.hysteresis_change(star1, k1, star2, k2)
    gain, loss = change_filters.filter_change(
        gain.updateMask(common), loss.updateMask(common), projection
    )

    pixel_area = ee.Image.pixelArea()
    stacked = ee.Image.cat(
        [
            pixel_area.multiply(water1).rename("water_p1"),
            pixel_area.multiply(water2).rename("water_p2"),
        ]
    )

    for ring in ring_definitions():
        index = ring["index"]
        in_ring = rings.eq(index)
        stacked = stacked.addBands(
            [
                pixel_area.updateMask(in_ring).rename(f"r{index}_land"),
                pixel_area.multiply(built1).updateMask(in_ring).rename(f"r{index}_built1"),
                pixel_area.multiply(built2).updateMask(in_ring).rename(f"r{index}_built2"),
                pixel_area.updateMask(gain.And(in_ring)).rename(f"r{index}_gain"),
                pixel_area.updateMask(loss.And(in_ring)).rename(f"r{index}_loss"),
            ]
        )

    totals = stacked.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=scale,
        maxPixels=1e10,
        bestEffort=True,
        # One band per ring per metric puts ~20 bands through the reducer, which
        # overruns Earth Engine's per-tile memory budget at 10 m. tileScale
        # subdivides the work; it costs wall time, not accuracy.
        tileScale=4,
    ).getInfo()

    def km2(key: str) -> float:
        return round((totals.get(key) or 0) / 1e6, 3)

    ring_stats = []
    for ring in ring_definitions():
        index = ring["index"]
        built1_km2 = km2(f"r{index}_built1")
        built2_km2 = km2(f"r{index}_built2")
        land_km2 = km2(f"r{index}_land")
        ring_stats.append(
            {
                "index": index,
                "label": ring["label"],
                "land_km2": land_km2,
                "built_up_km2_period1": built1_km2,
                "built_up_km2_period2": built2_km2,
                "gain_km2": km2(f"r{index}_gain"),
                "loss_km2": km2(f"r{index}_loss"),
                "growth_pct": round((built2_km2 - built1_km2) / built1_km2 * 100, 1)
                if built1_km2 > 0
                else None,
                # Share of the ring's land that is built-up. This is the number
                # that says how saturated the lake margin already is.
                "built_up_share_period1_pct": round(built1_km2 / land_km2 * 100, 1)
                if land_km2 > 0
                else None,
                "built_up_share_period2_pct": round(built2_km2 / land_km2 * 100, 1)
                if land_km2 > 0
                else None,
            }
        )

    water_p1, water_p2 = km2("water_p1"), km2("water_p2")
    return {
        "rings": ring_stats,
        "water": {
            "area_km2_period1": water_p1,
            "area_km2_period2": water_p2,
            "change_km2": round(water_p2 - water_p1, 3),
            "change_pct": round((water_p2 - water_p1) / water_p1 * 100, 1)
            if water_p1 > 0
            else None,
        },
    }
