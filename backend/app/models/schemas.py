from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app import config
from app.services.imagery import EARLIEST_ANALYSIS_YEAR, latest_complete_dry_season_year


class Aoi(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class ResolvedPeriod(BaseModel):
    year: int
    start: str
    end: str


class ChangeDetectionRequest(BaseModel):
    """A comparison of two dry seasons, identified by the year each begins in.

    Free start/end dates used to be accepted and were a trap: comparing November
    against April reads seasonal phenology as urban growth, and nothing
    downstream can undo that. A year expands to a fixed post-monsoon window, so
    both composites always sample the same part of the agricultural calendar.
    """

    aoi: Aoi
    year1: int = Field(..., ge=EARLIEST_ANALYSIS_YEAR)
    year2: int = Field(..., ge=EARLIEST_ANALYSIS_YEAR)
    cloud_threshold: int = Field(default=config.DEFAULT_CLOUD_THRESHOLD, ge=0, le=100)
    # On by default: this prototype is scoped to the Bhoj Wetland, where distance
    # from the shoreline is the question the whole analysis exists to answer.
    include_wetland: bool = True

    @model_validator(mode="after")
    def check_years(self):
        latest = latest_complete_dry_season_year()
        for label, year in (("year1", self.year1), ("year2", self.year2)):
            if year > latest:
                raise ValueError(
                    f"{label}={year} has no completed dry season yet. The most recent "
                    f"available year is {latest}."
                )
        if self.year1 >= self.year2:
            raise ValueError("year1 must be earlier than year2")
        return self


class ChangeStats(BaseModel):
    built_up_km2_period1: float
    built_up_km2_period2: float
    # gain and loss exclude change patches below min_change_unit_m2, so
    # gain - loss does not reconcile with net_change: sub-threshold change is
    # dropped from both while net_change is the plain classification difference.
    gain_km2: float
    loss_km2: float
    gain_km2_unfiltered: float
    loss_km2_unfiltered: float
    net_change_km2: float
    net_change_pct: float | None
    min_change_unit_m2: float
    # Share of raw change discarded as too small to be credible. High values mean
    # the classification was unstable between these two periods, which is a
    # warning about the result rather than a property of the landscape.
    change_filtered_pct: float | None
    # How far inside Eq. 3's bounds a pixel had to sit before a change call was
    # made. 0 means change was declared by plain comparison.
    change_confidence_margin: float
    # Whether period 1 was corrected onto period 2's radiometric scale. Without
    # it, growth over a multi-year gap reads roughly double.
    radiometrically_normalised: bool
    # In an expanding city, loss_km2 is closer to a measure of classifier error
    # than to demolition. Present so clients label the loss layer honestly.
    loss_is_error_estimate: bool = True


class RingStats(BaseModel):
    index: int
    label: str
    land_km2: float
    built_up_km2_period1: float
    built_up_km2_period2: float
    gain_km2: float
    loss_km2: float
    growth_pct: float | None
    built_up_share_period1_pct: float | None
    built_up_share_period2_pct: float | None


class WaterStats(BaseModel):
    area_km2_period1: float
    area_km2_period2: float
    change_km2: float
    change_pct: float | None


class WetlandAnalysis(BaseModel):
    rings: list[RingStats]
    water: WaterStats


class ChangeDetectionResponse(BaseModel):
    tiles: dict[str, str]
    export_urls: dict[str, str] = {}
    stats: ChangeStats
    aoi_km2: float
    periods: dict[str, ResolvedPeriod]
    wetland: WetlandAnalysis | None = None


class HealthResponse(BaseModel):
    status: str
    earth_engine: str
    detail: Any = None
