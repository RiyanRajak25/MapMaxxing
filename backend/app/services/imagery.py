import calendar
from datetime import date

import ee

from app.exceptions import NoImageryError

COLLECTION_ID = "COPERNICUS/S2_SR_HARMONIZED"

# Earth Engine's Sentinel-2 Level-2A (surface reflectance) archive starts here.
# Level-1C reaches back to 2015, but L2A is what gives us atmospherically
# corrected reflectance, which AMCBI's fixed k=0.6 constraint depends on.
L2A_ARCHIVE_START = "2017-03-28"

# Sentinel-2 Scene Classification Layer classes to discard.
# 3 = cloud shadow, 8 = cloud medium probability, 9 = cloud high probability,
# 10 = thin cirrus, 11 = snow/ice.
SCL_MASK_CLASSES = [3, 8, 9, 10, 11]

# Surface reflectance bands are stored as integers scaled by 10000.
REFLECTANCE_SCALE = 0.0001

# Blue, Red, NIR, SWIR1 are what AMCBI needs. Green (B3) is carried as well so
# MNDWI can delineate water for the wetland analysis.
AMCBI_BANDS = ["B2", "B3", "B4", "B8", "B11"]

# Periods are chosen as a YEAR, not a date range, and the window is fixed to the
# post-monsoon dry season that starts in that year: 1 November to end of
# February. This is not a convenience - it is the only mitigation that exists for
# the systematic seasonal bias. Two composites of the SAME dry season, split in
# half, still disagree by several percent on how much of a city is built-up, and
# no filter can remove that because it is a shift in the classification level
# rather than noise around it. Fixing the calendar window makes both periods
# sample the same phenology, which cancels most of it. Free date ranges made it
# trivially easy to compare November against April and read the difference as
# urban growth.
DRY_SEASON_START_MONTH = 11
DRY_SEASON_END_MONTH = 2

# Sentinel-2 L2A opens 2017-03-28, so the dry season beginning November 2017 is
# the first one fully covered.
EARLIEST_ANALYSIS_YEAR = 2017


def dry_season_window(year: int) -> tuple[str, str]:
    """The post-monsoon dry season beginning in `year`: 1 Nov to end of Feb."""
    end_year = year + 1
    last_day = calendar.monthrange(end_year, DRY_SEASON_END_MONTH)[1]
    return (
        f"{year}-{DRY_SEASON_START_MONTH:02d}-01",
        f"{end_year}-{DRY_SEASON_END_MONTH:02d}-{last_day:02d}",
    )


def latest_complete_dry_season_year(today: date | None = None) -> int:
    """The most recent year whose dry season has finished.

    A season that is still open would composite fewer scenes than the one it is
    compared against, which is exactly the imbalance that destabilises the
    classification level.
    """
    today = today or date.today()
    # The season starting in year Y closes at the end of February in Y+1.
    return today.year - 1 if today.month > DRY_SEASON_END_MONTH else today.year - 2


def _mask_clouds(image: ee.Image) -> ee.Image:
    """Mask cloud, shadow, cirrus and snow pixels using the SCL band."""
    scl = image.select("SCL")
    mask = ee.Image.constant(1)
    for scl_class in SCL_MASK_CLASSES:
        mask = mask.And(scl.neq(scl_class))
    return image.updateMask(mask)


def build_composite(aoi: ee.Geometry, start: str, end: str, cloud_threshold: int, period_label: str) -> ee.Image:
    """Build a cloud-masked median composite for one AOI and date range.

    Uses the HARMONIZED collection so that scenes before and after the
    January 2022 processing baseline change (which introduced a +1000
    reflectance offset) are directly comparable - essential for multi-year
    change detection.
    """
    if end <= L2A_ARCHIVE_START:
        raise NoImageryError(
            f"{period_label} ({start} to {end}) ends before the Sentinel-2 surface reflectance "
            f"archive begins on {L2A_ARCHIVE_START}. Choose a later period."
        )

    collection = (
        ee.ImageCollection(COLLECTION_ID)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud_threshold))
    )

    scene_count = collection.size().getInfo()
    if scene_count == 0:
        hint = "Try widening the date range or raising the cloud threshold."
        if start < L2A_ARCHIVE_START:
            hint = (
                f"Note this period starts before the Sentinel-2 surface reflectance archive "
                f"({L2A_ARCHIVE_START}), so most of it has no data. " + hint
            )
        raise NoImageryError(
            f"No Sentinel-2 scenes found for {period_label} ({start} to {end}) with cloud cover "
            f"under {cloud_threshold}%. {hint}"
        )

    composite = (
        collection.map(_mask_clouds)
        .select(AMCBI_BANDS)
        .median()
        .multiply(REFLECTANCE_SCALE)
        .clip(aoi)
    )
    return composite


def native_projection(aoi: ee.Geometry, start: str, end: str) -> ee.Projection:
    """The 10 m UTM grid the scenes actually sit on.

    A median composite carries Earth Engine's default 1-degree WGS84 projection,
    not the imagery's. Any neighbourhood or connectivity operation runs in
    whatever projection it inherits, so counting "11 connected pixels" on the
    default grid would measure something else entirely. Reprojecting to the
    scene's own UTM keeps pixel counts honest, and sidesteps the ~9% web-Mercator
    distance stretch at this latitude.

    B2 is used because it is one of the 10 m bands; B11 would give a 20 m grid.
    """
    scene = ee.Image(
        ee.ImageCollection(COLLECTION_ID).filterBounds(aoi).filterDate(start, end).first()
    )
    return scene.select("B2").projection()
