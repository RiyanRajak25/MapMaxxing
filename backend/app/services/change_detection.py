import ee

from app import config
from app.exceptions import AoiTooLargeError
from app.services import amcbi as amcbi_service
from app.services import change_filters
from app.services import normalization
from app.services import wetland as wetland_service
from app.services.gee_auth import require_earth_engine
from app.services.imagery import build_composite, dry_season_window, native_projection

# Every result layer is a masked binary image: 1 where the class is present,
# transparent everywhere else. min/max are set explicitly so Earth Engine never
# guesses a stretch, and each palette holds a single colour so the layer reads as
# one flat class rather than a gradient.
#
# Rendering the continuous AMCBI index here instead would fill the whole AOI -
# non-built-up is negative and maps to the dark end of any palette, which paints
# an opaque sheet over the basemap and every layer under it.
_FLAT = {"min": 0, "max": 1}

BUILT_UP_P1_VIS = {**_FLAT, "palette": ["ffc300"]}  # amber, the earlier state
BUILT_UP_P2_VIS = {**_FLAT, "palette": ["00e5ff"]}  # cyan, the later state
GAIN_VIS = {**_FLAT, "palette": ["ff2d2d"]}  # red, new built-up
LOSS_VIS = {**_FLAT, "palette": ["9e9e9e"]}  # grey, built-up no longer detected
WATER_VIS = {**_FLAT, "palette": ["1d7fe0"]}

# True-color RGB visualization for Sentinel-2 surface reflectance (B4=Red, B3=Green, B2=Blue)
RGB_VIS = {"bands": ["B4", "B3", "B2"], "min": 0.0, "max": 0.3}

# Sentinel-2 native resolution for the bands AMCBI uses.
ANALYSIS_SCALE = 10


def _tile_url(image: ee.Image, vis_params: dict) -> str:
    """Return an XYZ tile URL template rendered on demand by Earth Engine."""
    return image.getMapId(vis_params)["tile_fetcher"].url_format


def _shapefile_urls(aoi: ee.Geometry, layers: dict[str, ee.Image]) -> dict[str, str]:
    """Return one Earth Engine SHP URL per vector layer."""
    urls = {}
    for layer_name, image in layers.items():
        vectors = image.selfMask().rename("value").reduceToVectors(
            geometry=aoi,
            scale=ANALYSIS_SCALE,
            geometryType="polygon",
            eightConnected=False,
            labelProperty="value",
            reducer=ee.Reducer.countEvery(),
            maxPixels=1e9,
            bestEffort=True,
        )
        urls[layer_name] = vectors.getDownloadURL(
            filetype="shp",
            filename=layer_name,
            selectors=["value"],
        )
    return urls


def _validate_aoi_size(aoi: ee.Geometry) -> float:
    area_km2 = aoi.area(maxError=1).divide(1e6).getInfo()
    if area_km2 > config.MAX_AOI_KM2:
        raise AoiTooLargeError(
            f"The selected area is {area_km2:,.0f} km2, which exceeds the {config.MAX_AOI_KM2:,.0f} km2 "
            "limit for a single analysis. Draw a smaller area."
        )
    return area_km2


def run_change_detection(
    aoi_geojson: dict,
    year1: int,
    year2: int,
    cloud_threshold: int,
    include_wetland: bool = False,
) -> dict:
    """Detect built-up change between two dry seasons over an AOI.

    Periods are years, not free date ranges: each expands to the post-monsoon
    dry season beginning in that year, so both composites sample the same
    phenology. See imagery.dry_season_window for why that is not optional.

    Returns tile URL templates for the map layers plus area statistics.
    """
    require_earth_engine()

    aoi = ee.Geometry(aoi_geojson)
    area_km2 = _validate_aoi_size(aoi)

    start1, end1 = dry_season_window(year1)
    start2, end2 = dry_season_window(year2)

    composite1 = build_composite(aoi, start1, end1, cloud_threshold, f"{year1} dry season")
    composite2 = build_composite(aoi, start2, end2, cloud_threshold, f"{year2} dry season")

    # Put the earlier period on the later one's radiometric scale before
    # classifying anything. Uncorrected, the 2017 composite under-detects
    # built-up by ~12% relative to 2023 on ground that cannot have changed,
    # which roughly doubles reported growth. See normalization.py.
    if config.NORMALIZE_PERIODS:
        composite1 = normalization.normalise_to(composite1, composite2, aoi, ANALYSIS_SCALE)

    star1, k1 = amcbi_service.constraint_terms(composite1)
    star2, k2 = amcbi_service.constraint_terms(composite2)

    built_up1 = amcbi_service.built_up_with_margin(star1, k1, 0)
    built_up2 = amcbi_service.built_up_with_margin(star2, k2, 0)

    # Restrict every layer and statistic to pixels that are cloud-free in BOTH
    # periods. Without this, the two built-up areas would be measured over
    # different pixel sets and their difference would conflate real change with
    # differences in cloud coverage.
    common_mask = built_up1.mask().And(built_up2.mask())
    built_up1 = built_up1.updateMask(common_mask)
    built_up2 = built_up2.updateMask(common_mask)

    change = built_up2.subtract(built_up1)
    gain_raw = change.eq(1)
    loss_raw = change.eq(-1)

    # Two independent hard thresholds are being compared, so the noise of both
    # periods lands in the difference. Two filters, addressing different halves
    # of that: hysteresis withholds a change call from pixels hovering on a
    # bound, and the minimum mapping unit drops patches too small to be real.
    # Unfiltered, a growing city reports ~20% of itself demolished. See
    # app/services/change_filters.py for the measurements behind both.
    projection = native_projection(aoi, start2, end2)
    gain, loss = change_filters.hysteresis_change(star1, k1, star2, k2)
    # Hysteresis works from the raw index terms, which carry each period's own
    # cloud mask, so re-apply the both-periods mask for the same reason as above.
    gain = gain.updateMask(common_mask)
    loss = loss.updateMask(common_mask)
    gain, loss = change_filters.filter_change(gain, loss, projection)

    stats = _compute_area_stats(aoi, built_up1, built_up2, gain, loss, gain_raw, loss_raw)

    tiles = {
        # True-color RGB satellite composites for visually inspecting each period
        "period1_rgb": _tile_url(composite1, RGB_VIS),
        "period2_rgb": _tile_url(composite2, RGB_VIS),
        # selfMask() drops the zeros, so non-built-up stays transparent and the
        # basemap shows through.
        "period1_amcbi": _tile_url(built_up1.selfMask(), BUILT_UP_P1_VIS),
        "period2_amcbi": _tile_url(built_up2.selfMask(), BUILT_UP_P2_VIS),
        "gain": _tile_url(gain.selfMask(), GAIN_VIS),
        "loss": _tile_url(loss.selfMask(), LOSS_VIS),
    }

    export_layers = {
        f"built_up_{year1}": built_up1,
        f"built_up_{year2}": built_up2,
        "new_built_up": gain,
        "no_longer_detected": loss,
    }

    result = {
        "tiles": tiles,
        "export_urls": {},
        "stats": stats,
        "aoi_km2": round(area_km2, 2),
        # The resolved windows, so a client never has to guess what "2017" meant.
        "periods": {
            "period1": {"year": year1, "start": start1, "end": end1},
            "period2": {"year": year2, "start": start2, "end": end2},
        },
    }

    if include_wetland:
        tiles["water"] = _tile_url(
            wetland_service.water_mask(composite1).selfMask(), WATER_VIS
        )
        export_layers["water"] = wetland_service.water_mask(composite1)
        result["wetland"] = wetland_service.analyse(aoi, composite1, composite2, projection)

    result["export_urls"] = _shapefile_urls(aoi, export_layers)

    return result


def _compute_area_stats(
    aoi: ee.Geometry,
    built_up1: ee.Image,
    built_up2: ee.Image,
    gain: ee.Image,
    loss: ee.Image,
    gain_raw: ee.Image,
    loss_raw: ee.Image,
) -> dict:
    """Sum the area of each layer in one Earth Engine round trip."""
    pixel_area = ee.Image.pixelArea()
    stacked = (
        pixel_area.multiply(built_up1).rename("built_up_period1")
        .addBands(pixel_area.multiply(built_up2).rename("built_up_period2"))
        .addBands(pixel_area.updateMask(gain.selfMask()).rename("gain"))
        .addBands(pixel_area.updateMask(loss.selfMask()).rename("loss"))
        .addBands(pixel_area.updateMask(gain_raw.selfMask()).rename("gain_raw"))
        .addBands(pixel_area.updateMask(loss_raw.selfMask()).rename("loss_raw"))
    )

    result = stacked.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=ANALYSIS_SCALE,
        maxPixels=1e9,
        bestEffort=True,
        # Six bands at 10 m over a large AOI overruns the per-tile memory budget.
        tileScale=4,
    ).getInfo()

    def km2(key: str) -> float:
        return round((result.get(key) or 0) / 1e6, 3)

    built1 = km2("built_up_period1")
    built2 = km2("built_up_period2")
    net_change = round(built2 - built1, 3)
    net_change_pct = round((net_change / built1) * 100, 1) if built1 > 0 else None

    gain_km2, loss_km2 = km2("gain"), km2("loss")
    gain_raw_km2, loss_raw_km2 = km2("gain_raw"), km2("loss_raw")

    # How much of the raw change failed one of the two credibility filters -
    # either it sat on a decision boundary or its patch was too small to be real.
    # This is a direct read on how unstable the classification was over this AOI
    # and date pair; over Bhopal it runs to about 72% of the raw change.
    raw_total = gain_raw_km2 + loss_raw_km2
    filtered_out_pct = (
        round((raw_total - gain_km2 - loss_km2) / raw_total * 100, 1) if raw_total > 0 else None
    )

    return {
        "built_up_km2_period1": built1,
        "built_up_km2_period2": built2,
        # Gain and loss exclude change patches below the minimum mapping unit,
        # so they deliberately do NOT reconcile with net_change, which is the
        # plain difference of the two classifications.
        "gain_km2": gain_km2,
        "loss_km2": loss_km2,
        "gain_km2_unfiltered": gain_raw_km2,
        "loss_km2_unfiltered": loss_raw_km2,
        "net_change_km2": net_change,
        "net_change_pct": net_change_pct,
        "min_change_unit_m2": change_filters.min_change_unit_m2(ANALYSIS_SCALE),
        "change_filtered_pct": filtered_out_pct,
        "change_confidence_margin": change_filters.CHANGE_CONFIDENCE_MARGIN,
        "radiometrically_normalised": config.NORMALIZE_PERIODS,
        # Buildings do not disappear over a few years, so in an expanding city
        # measured loss is very close to a direct readout of the classifier's
        # commission error under exactly this AOI and date pair. It is a
        # diagnostic first and a finding second - see change_filters.py.
        "loss_is_error_estimate": True,
    }
