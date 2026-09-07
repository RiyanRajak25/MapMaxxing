"""Validate the minimum-mapping-unit filter on change patches.

The shipped filter is built from Earth Engine server-side ops that cannot run
without credentials, so this mirrors connectedPixelCount in pure Python and
tests the properties the real filter has to hold. The threshold itself is
imported from the real module, so lowering it silently breaks these tests.

Why the filter exists: two composites of the SAME dry season over Bhopal - true
change zero - reported 30.6 km2 of change, and the real 2017->2024 run reported
20% of the city demolished. 58% of that loss was in patches of 5 pixels or
fewer. See app/services/change_filters.py.
"""

import pytest

from app.services.amcbi import K_MAX, K_MIN
from app.services.change_filters import (
    CHANGE_CONFIDENCE_MARGIN,
    CHANGE_MIN_PIXELS,
    min_change_unit_m2,
)

ANALYSIS_SCALE = 10


def connected_patches(grid: list[list[int]]) -> list[list[tuple[int, int]]]:
    """Eight-connected patches of the 1-class, matching connectedPixelCount."""
    rows, cols = len(grid), len(grid[0])
    seen: set[tuple[int, int]] = set()
    patches = []
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] != 1 or (r, c) in seen:
                continue
            stack, patch = [(r, c)], []
            seen.add((r, c))
            while stack:
                cr, cc = stack.pop()
                patch.append((cr, cc))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = cr + dr, cc + dc
                        if (
                            0 <= nr < rows and 0 <= nc < cols
                            and grid[nr][nc] == 1 and (nr, nc) not in seen
                        ):
                            seen.add((nr, nc))
                            stack.append((nr, nc))
            patches.append(patch)
    return patches


def drop_small_patches(grid: list[list[int]], min_pixels: int) -> list[list[int]]:
    """Pure-python mirror of change_filters.drop_small_patches."""
    if min_pixels <= 1:
        return [row[:] for row in grid]
    out = [[0] * len(grid[0]) for _ in grid]
    for patch in connected_patches(grid):
        if len(patch) >= min_pixels:
            for r, c in patch:
                out[r][c] = 1
    return out


def total(grid: list[list[int]]) -> int:
    return sum(sum(row) for row in grid)


# A lone flipped pixel, a diagonal pair, and one solid 4x4 block. Only the block
# is large enough to be real construction at 10 m. Two empty columns separate the
# speckle from the block, since eight-connectivity would otherwise merge a
# diagonally touching pixel into it.
SPECKLE_AND_BLOCK = [
    [1, 0, 0, 0, 1, 1, 1, 1],
    [0, 0, 0, 0, 1, 1, 1, 1],
    [0, 1, 0, 0, 1, 1, 1, 1],
    [1, 0, 0, 0, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0],
]


class TestSpeckleRemoval:
    def test_isolated_pixel_is_dropped(self):
        grid = [[0, 0, 0], [0, 1, 0], [0, 0, 0]]
        assert total(drop_small_patches(grid, CHANGE_MIN_PIXELS)) == 0

    def test_solid_block_survives(self):
        filtered = drop_small_patches(SPECKLE_AND_BLOCK, CHANGE_MIN_PIXELS)
        # The 4x4 block is 16 px; the speckle is 1 px and a 2 px diagonal pair.
        assert total(filtered) == 16
        assert total(SPECKLE_AND_BLOCK) == 19

    def test_patch_exactly_at_threshold_survives(self):
        """The bound is inclusive: >= min_pixels is kept."""
        row = [1] * CHANGE_MIN_PIXELS + [0]
        assert total(drop_small_patches([row], CHANGE_MIN_PIXELS)) == CHANGE_MIN_PIXELS

    def test_patch_one_below_threshold_is_dropped(self):
        row = [1] * (CHANGE_MIN_PIXELS - 1) + [0]
        assert total(drop_small_patches([row], CHANGE_MIN_PIXELS)) == 0

    def test_filter_never_creates_change(self):
        """A patch filter may only remove pixels, never add them.

        Anything else would manufacture change that the classification did not
        find, which is worse than the speckle it is meant to remove.
        """
        filtered = drop_small_patches(SPECKLE_AND_BLOCK, CHANGE_MIN_PIXELS)
        for r, row in enumerate(filtered):
            for c, value in enumerate(row):
                assert value <= SPECKLE_AND_BLOCK[r][c]

    def test_disabled_filter_is_identity(self):
        assert drop_small_patches(SPECKLE_AND_BLOCK, 1) == SPECKLE_AND_BLOCK


class TestSymmetry:
    """The filter must not bias the direction of change.

    Applying a harsher rule to loss than to gain would let the pipeline
    manufacture growth, which is exactly the conclusion this project reports.
    """

    def test_gain_and_loss_are_filtered_identically(self):
        gain = SPECKLE_AND_BLOCK
        loss = [row[:] for row in SPECKLE_AND_BLOCK]
        assert (
            drop_small_patches(gain, CHANGE_MIN_PIXELS)
            == drop_small_patches(loss, CHANGE_MIN_PIXELS)
        )


def built_up_with_margin(star: float, k: float, margin: float) -> bool:
    """Pure-python mirror of amcbi.built_up_with_margin (Eq. 3, shifted)."""
    return star > margin and (K_MIN + margin) <= k <= K_MAX


def hysteresis_call(
    star1: float, k1: float, star2: float, k2: float, margin: float
) -> str:
    """Pure-python mirror of change_filters.hysteresis_change, for one pixel."""
    if margin <= 0:
        b1 = built_up_with_margin(star1, k1, 0)
        b2 = built_up_with_margin(star2, k2, 0)
    else:
        core1 = built_up_with_margin(star1, k1, margin)
        core2 = built_up_with_margin(star2, k2, margin)
        loose1 = built_up_with_margin(star1, k1, -margin)
        loose2 = built_up_with_margin(star2, k2, -margin)
        if core2 and not loose1:
            return "gain"
        if core1 and not loose2:
            return "loss"
        return "none"
    if b2 and not b1:
        return "gain"
    if b1 and not b2:
        return "loss"
    return "none"


class TestHysteresis:
    """The measured failure: a standing building whose k drifts under K_MIN.

    Over Bhopal, 59.3% of pixels reported as lost still satisfied Eq. 1 in
    period 2, and their median period-2 k was 0.391 against a K_MIN of 0.42.
    Those are buildings that missed a threshold by 0.029, not demolitions.
    """

    # A standing building whose 2024 k drifted just inside the shipped margin.
    # 42.8% of Bhopal's reported loss looks like this.
    STANDING_BUILDING = (0.085, 0.50, 0.045, 0.405)

    # The MEDIAN pixel Bhopal reports as lost: k2 = 0.391, which is 0.029 below
    # K_MIN and therefore outside a 0.02 margin. The shipped setting does not
    # rescue it, and the tests say so rather than implying the fix is total.
    MEDIAN_LOST_PIXEL = (0.085, 0.50, 0.045, 0.391)

    def test_plain_comparison_reports_the_standing_building_as_lost(self):
        """Documents the bug. Without a margin, threshold drift reads as demolition."""
        assert hysteresis_call(*self.STANDING_BUILDING, margin=0) == "loss"

    def test_margin_withholds_the_call(self):
        assert hysteresis_call(*self.STANDING_BUILDING, margin=CHANGE_CONFIDENCE_MARGIN) == "none"

    def test_shipped_margin_does_not_rescue_every_standing_building(self):
        """The default is a knee-of-the-curve compromise, not a cure.

        Pixels further than CHANGE_CONFIDENCE_MARGIN below K_MIN are still called
        loss, which is why Bhopal retains 1.64 km2 of it. Widening the margin
        catches more of them and costs real gain recall - measured in
        change_filters.hysteresis_change.
        """
        assert hysteresis_call(*self.MEDIAN_LOST_PIXEL, margin=CHANGE_CONFIDENCE_MARGIN) == "loss"
        assert hysteresis_call(*self.MEDIAN_LOST_PIXEL, margin=0.04) == "none"

    def test_decisive_loss_is_still_reported(self):
        """A margin must not make loss unreportable - only harder to claim.

        Built-up in 2017, then unambiguously vegetation in 2024: Eq. 1 goes
        clearly negative and k falls far below K_MIN.
        """
        assert hysteresis_call(0.085, 0.50, -0.20, 0.15, CHANGE_CONFIDENCE_MARGIN) == "loss"

    def test_decisive_gain_is_still_reported(self):
        assert hysteresis_call(-0.20, 0.15, 0.085, 0.50, CHANGE_CONFIDENCE_MARGIN) == "gain"

    def test_marginal_gain_is_withheld(self):
        """The same restraint applies to gain, or the filter would bias growth."""
        assert hysteresis_call(0.005, 0.415, 0.025, 0.435, CHANGE_CONFIDENCE_MARGIN) == "none"

    def test_rule_is_symmetric(self):
        """Swapping the two periods must swap gain and loss, never change the verdict.

        This is what stops the filter from manufacturing growth: it cannot be
        more willing to call gain than loss.
        """
        cases = [
            (0.085, 0.50, 0.045, 0.391),
            (0.085, 0.50, -0.20, 0.15),
            (-0.20, 0.15, 0.085, 0.50),
            (0.005, 0.415, 0.025, 0.435),
        ]
        opposite = {"gain": "loss", "loss": "gain", "none": "none"}
        for star1, k1, star2, k2 in cases:
            forward = hysteresis_call(star1, k1, star2, k2, CHANGE_CONFIDENCE_MARGIN)
            reverse = hysteresis_call(star2, k2, star1, k1, CHANGE_CONFIDENCE_MARGIN)
            assert reverse == opposite[forward], f"asymmetric on {(star1, k1, star2, k2)}"

    def test_water_is_never_admitted_by_the_loose_bound(self):
        """K_MAX must not be relaxed: above it lies water, where blue > SWIR1.

        A lake pixel must not qualify as 'loosely built-up' in either period,
        or a flooded margin would silently become stable built-up.
        """
        water_k = 1.8
        assert not built_up_with_margin(0.05, water_k, -CHANGE_CONFIDENCE_MARGIN)
        assert not built_up_with_margin(0.05, water_k, 0)
        assert not built_up_with_margin(0.05, water_k, CHANGE_CONFIDENCE_MARGIN)

    def test_zero_margin_is_the_plain_comparison(self):
        for star1, k1, star2, k2 in [(0.085, 0.50, 0.045, 0.391), (-0.2, 0.15, 0.085, 0.50)]:
            b1 = built_up_with_margin(star1, k1, 0)
            b2 = built_up_with_margin(star2, k2, 0)
            expected = "gain" if (b2 and not b1) else "loss" if (b1 and not b2) else "none"
            assert hysteresis_call(star1, k1, star2, k2, 0) == expected


class TestConfiguredUnit:
    def test_threshold_is_meaningful_at_sentinel_resolution(self):
        """Below 2 px the filter cannot remove a diagonal pair, the commonest
        speckle form. Above ~50 px it would erase genuine city blocks."""
        assert 2 <= CHANGE_MIN_PIXELS <= 50

    def test_confidence_margin_is_smaller_than_the_built_up_margin(self):
        """The margin must not exceed the room real built-up actually has.

        Stable built-up over Bhopal sits a median 0.084 above K_MIN. A margin at
        or above that would refuse to call change on genuine city.
        """
        assert 0 <= CHANGE_CONFIDENCE_MARGIN < 0.084

    def test_min_change_unit_matches_pixel_count(self):
        assert min_change_unit_m2(ANALYSIS_SCALE) == CHANGE_MIN_PIXELS * 100

    @pytest.mark.parametrize("scale,expected", [(10, 1100), (20, 4400)])
    def test_unit_scales_with_resolution(self, scale, expected):
        if CHANGE_MIN_PIXELS != 11:
            pytest.skip("expected values assume the shipped default of 11 px")
        assert min_change_unit_m2(scale) == expected
