"""Validate the AMCBI constraint logic.

Two separate claims are tested here, and they need different k bounds:

1. That app/services/amcbi.py reads the paper's Eq. 3 correctly. The published
   constraint prints as "0.6 >= k >= 1", which is unsatisfiable; we read it as
   0.6 <= k <= 1. Tested against the paper's own Fig. 2(a) spectra using the
   paper's published bounds.

2. That the shipped default bounds actually work on Sentinel-2 L2A surface
   reflectance. The paper's 0.6 does not, because its Fig. 2 reflectances are on
   a top-of-atmosphere scale (built-up blue ~0.21) roughly double what L2A
   reports for built-up (~0.10). Tested against reflectances measured over known
   built-up locations in Bhopal.

The reference implementation below mirrors amcbi.compute_amcbi. It exists
because the real implementation is built from Earth Engine server-side ops that
cannot run without credentials; the bounds are imported from the real module so
drift in those values is caught here.
"""

import pytest

from app.services.amcbi import K_MAX, K_MIN, PAPER_K_MAX, PAPER_K_MIN

# ---------------------------------------------------------------------------
# 1. The paper's Eq. 3, on the paper's own Fig. 2(a) spectra
# ---------------------------------------------------------------------------

# (blue, red, nir, swir1)
PAPER_SPECTRA = {
    "built_up": (0.21, 0.24, 0.27, 0.31),
    "bare_soil": (0.19, 0.28, 0.38, 0.47),
    "rock": (0.19, 0.26, 0.33, 0.35),
    "vegetation": (0.09, 0.08, 0.42, 0.22),
    "water": (0.12, 0.06, 0.03, 0.02),
}

PAPER_EXPECTED_BUILT_UP = {
    "built_up": True,
    "bare_soil": False,
    "rock": False,
    "vegetation": False,
    "water": False,
}

# ---------------------------------------------------------------------------
# 2. Measured Sentinel-2 L2A reflectance over known built-up Bhopal locations
#    (2023-11-01 to 2024-02-29 median composite, via scripts/diagnose_amcbi.py)
# ---------------------------------------------------------------------------

BHOPAL_BUILT_UP = {
    "New Market / TT Nagar": (0.103, 0.153, 0.208, 0.222),
    "Old City / Chowk": (0.118, 0.154, 0.177, 0.221),
    "Habibganj station area": (0.098, 0.122, 0.157, 0.202),
    "MP Nagar": (0.097, 0.128, 0.161, 0.173),
}

# 75th percentile of k across the Bhopal AOI, dominated by soil and vegetation.
BHOPAL_SCENE_K_P75 = 0.389


def amcbi_reference(blue, red, nir, swir1, k_min, k_max):
    """Pure-python mirror of compute_amcbi (Eq. 1-3)."""
    amcbi_star = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue))  # Eq. 1
    k = blue / swir1  # Eq. 2
    if amcbi_star > 0 and k_min <= k <= k_max:  # Eq. 3
        return amcbi_star
    return -abs(amcbi_star)


class TestPaperInterpretation:
    """Eq. 3 as published, against the paper's own spectra."""

    @pytest.mark.parametrize("cover", sorted(PAPER_SPECTRA))
    def test_classification_matches_paper(self, cover):
        amcbi = amcbi_reference(*PAPER_SPECTRA[cover], PAPER_K_MIN, PAPER_K_MAX)
        assert (amcbi > 0) is PAPER_EXPECTED_BUILT_UP[cover], (
            f"{cover} classified as {'built-up' if amcbi > 0 else 'non-built-up'} "
            f"(AMCBI={amcbi:.3f})"
        )

    @pytest.mark.parametrize("cover", ["bare_soil", "rock"])
    def test_constraint_is_what_eliminates_bare_soil(self, cover):
        """Eq. 1 alone scores bare soil and rock positive; the constraint does the work.

        This is the paper's central claim: a plain normalized-difference index
        confuses these with built-up, and the k-ratio constraint separates them
        without any thresholding.
        """
        blue, red, nir, swir1 = PAPER_SPECTRA[cover]
        amcbi_star = ((swir1 + red) - (nir + blue)) / ((swir1 + red) + (nir + blue))
        assert amcbi_star > 0, "expected Eq. 1 alone to misclassify this cover as built-up"
        assert not (PAPER_K_MIN <= blue / swir1 <= PAPER_K_MAX)
        assert amcbi_reference(blue, red, nir, swir1, PAPER_K_MIN, PAPER_K_MAX) < 0


class TestL2ACalibration:
    """The shipped defaults, against real Sentinel-2 L2A reflectance."""

    @pytest.mark.parametrize("location", sorted(BHOPAL_BUILT_UP))
    def test_known_built_up_is_classified_built_up(self, location):
        amcbi = amcbi_reference(*BHOPAL_BUILT_UP[location], K_MIN, K_MAX)
        assert amcbi > 0, (
            f"{location} is dense urban but scored {amcbi:.3f}. If this fails, the k "
            f"bounds ({K_MIN}-{K_MAX}) are rejecting genuine built-up again."
        )

    @pytest.mark.parametrize("location", sorted(BHOPAL_BUILT_UP))
    def test_paper_bounds_would_reject_these(self, location):
        """Documents the bug this calibration fixes.

        Every one of these real urban locations fails the paper's k >= 0.6, which
        is why built-up area came out ~10x too low before recalibration.
        """
        assert amcbi_reference(*BHOPAL_BUILT_UP[location], PAPER_K_MIN, PAPER_K_MAX) < 0

    def test_default_k_min_separates_built_up_from_scene_background(self):
        lowest_built_up_k = min(b / s for b, _, _, s in BHOPAL_BUILT_UP.values())
        assert K_MIN < lowest_built_up_k, "K_MIN must sit below real built-up k"
        assert K_MIN > BHOPAL_SCENE_K_P75, (
            "K_MIN must sit above the soil/vegetation bulk of the scene, "
            "or bare soil leaks into the built-up class"
        )

    def test_k_max_excludes_water(self):
        """Water has blue > SWIR1, so k > 1. K_MAX at 1.0 keeps lakes out."""
        assert K_MAX <= 1.0


def test_output_stays_within_index_range():
    for spectrum in list(PAPER_SPECTRA.values()) + list(BHOPAL_BUILT_UP.values()):
        assert -1.0 <= amcbi_reference(*spectrum, K_MIN, K_MAX) <= 1.0
