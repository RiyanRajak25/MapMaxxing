"""Validate the fixed dry-season window that replaced free date ranges.

Free start/end dates were a trap: comparing November against April reads
seasonal phenology as urban growth, and no downstream filter can undo it. Two
composites of the same dry season split in half already disagree by several
percent on how much of a city is built-up; different seasons are far worse.

Fixing the window to 1 Nov - end of Feb is the only mitigation that exists for
that bias, so these tests guard it.
"""

from datetime import date

import pytest

from app.services.imagery import (
    EARLIEST_ANALYSIS_YEAR,
    L2A_ARCHIVE_START,
    dry_season_window,
    latest_complete_dry_season_year,
)


class TestDrySeasonWindow:
    def test_window_spans_the_new_year(self):
        assert dry_season_window(2017) == ("2017-11-01", "2018-02-28")

    def test_leap_year_february_is_handled(self):
        """2020 and 2024 have a 29th. Hardcoding the 28th would silently drop a
        day of imagery from exactly one period in a comparison."""
        assert dry_season_window(2019) == ("2019-11-01", "2020-02-29")
        assert dry_season_window(2023) == ("2023-11-01", "2024-02-29")

    @pytest.mark.parametrize("year", [2017, 2018, 2020, 2023, 2025])
    def test_every_window_is_the_same_length_of_calendar(self, year):
        """Both periods must sample the same months, or the comparison is
        measuring phenology rather than construction."""
        start, end = dry_season_window(year)
        assert start.endswith("-11-01")
        assert end[4:8] == "-02-"

    def test_earliest_year_starts_after_the_l2a_archive(self):
        start, _ = dry_season_window(EARLIEST_ANALYSIS_YEAR)
        assert start > L2A_ARCHIVE_START, (
            "the first offered year must be fully covered by Sentinel-2 L2A"
        )


class TestLatestCompleteYear:
    """A season still in progress composites fewer scenes than the one it is
    compared against, and that imbalance is exactly what destabilises the
    classification level."""

    @pytest.mark.parametrize(
        "today,expected",
        [
            (date(2026, 8, 10), 2025),   # mid-year: last winter is done
            (date(2026, 3, 1), 2025),    # the day after Feb closes
            (date(2026, 2, 28), 2024),   # 2025 season still open, fall back one
            (date(2026, 1, 15), 2024),   # mid-season, same
            (date(2025, 12, 1), 2024),   # 2025 season just started; 2024's closed
        ],
    )
    def test_only_closed_seasons_are_offered(self, today, expected):
        assert latest_complete_dry_season_year(today) == expected

    def test_latest_is_never_before_the_earliest(self):
        assert latest_complete_dry_season_year(date(2026, 8, 10)) >= EARLIEST_ANALYSIS_YEAR
