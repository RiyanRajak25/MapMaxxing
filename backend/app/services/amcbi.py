import ee

from app import config

# Constraint bounds on k = Blue / SWIR1.
#
# READING OF EQ. 3
# The published Eq. 3 prints the constraint as "(0.6 >= k >= 1)", which is
# unsatisfiable as written. The surrounding text resolves the intent: "The value
# of 'k' less than 0.6 represents non-built-up areas, whereas the 'k' value
# greater than 0.6 represents built-up areas." So it reads as K_MIN <= k <= K_MAX.
# The upper bound excludes water, where blue exceeds SWIR1.
#
# WHY THE DEFAULT IS NOT THE PAPER'S 0.6
# The paper's Fig. 2a reports built-up blue ~0.21 and SWIR1 ~0.31, giving
# k ~= 0.68. Measured on actual Sentinel-2 L2A surface reflectance over Bhopal,
# built-up gives blue ~0.10 and SWIR1 ~0.21, so k ~= 0.46-0.56. The paper's blue
# is roughly double true bottom-of-atmosphere reflectance, which is the signature
# of top-of-atmosphere values (Rayleigh scattering inflates blue; atmospheric
# correction removes it).
#
# Applying k >= 0.6 to L2A therefore rejects almost all genuine built-up: in a
# Bhopal composite, 48% of pixels passed Eq. 1 but only 2.3% passed k >= 0.6, and
# every hand-checked urban location failed. Built-up area came out at 8.9 km2 for
# a city that covers well over 100 km2.
#
# The default below is calibrated for L2A: it sits under the lowest measured
# built-up k (0.463) and above the 75th percentile of the scene (0.389), which is
# dominated by soil and vegetation. Override via AMCBI_K_MIN / AMCBI_K_MAX for
# other regions or a different reflectance basis - scripts/calibrate_k.py
# measures the right value for a given area.
K_MIN = config.AMCBI_K_MIN
K_MAX = config.AMCBI_K_MAX

# The values as published, kept so tests can verify the Eq. 3 reading against the
# paper's own top-of-atmosphere-scale spectra.
PAPER_K_MIN = 0.6
PAPER_K_MAX = 1.0

AMCBI_VIS = {"min": -1, "max": 1, "palette": ["000000", "1f4f4f", "00c8c8"]}
BUILT_UP_VIS = {"min": 0, "max": 1, "palette": ["000000", "00c8c8"]}


def constraint_terms(image: ee.Image) -> tuple[ee.Image, ee.Image]:
    """The two quantities Eq. 3 thresholds: AMCBI* (Eq. 1) and k (Eq. 2).

    Exposed separately because change detection needs to ask how FAR a pixel sits
    from each bound, not just which side of it. Both terms are hard-thresholded
    in Eq. 3, and a pixel sitting a hair above a bound in one period and a hair
    below it in the next produces a spurious change call.
    """
    blue = image.select("B2")
    red = image.select("B4")
    nir = image.select("B8")
    swir1 = image.select("B11")

    # Eq. 1 - spectral transformation
    amcbi_star = image.expression(
        "((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))",
        {"SWIR1": swir1, "RED": red, "NIR": nir, "BLUE": blue},
    ).rename("amcbi_star")

    # Eq. 2 - constraint parameter
    k = blue.divide(swir1).rename("k")

    return amcbi_star, k


def built_up_with_margin(amcbi_star: ee.Image, k: ee.Image, margin: float) -> ee.Image:
    """Eq. 3 shifted inward or outward by `margin`.

    margin > 0 demands the pixel clear both bounds with room to spare - decisive
    built-up. margin < 0 accepts anything close to the bounds, so its NEGATION is
    decisive non-built-up. margin = 0 is Eq. 3 exactly.

    K_MAX is deliberately never relaxed. Above it lies water, where blue exceeds
    SWIR1, and letting the lake edge into the built-up class would be a worse
    error than the boundary noise this is here to absorb.
    """
    return (
        amcbi_star.gt(margin)
        .And(k.gte(K_MIN + margin))
        .And(k.lte(K_MAX))
        .rename("built_up")
    )


def compute_amcbi(image: ee.Image) -> ee.Image:
    """Compute the Advanced Multispectral Constraint-Based Built-up Index.

    Expects a surface reflectance image with B2 (Blue), B4 (Red), B8 (NIR) and
    B11 (SWIR1). Returns a single band named 'AMCBI' in the range [-1, 1] where
    positive values are built-up. No thresholding is required: the constraint
    step forces spectrally-similar bare soil negative regardless of magnitude.
    """
    amcbi_star, k = constraint_terms(image)

    # Eq. 3 - constraint-based logical elimination
    constraint = amcbi_star.gt(0).And(k.gte(K_MIN)).And(k.lte(K_MAX))
    amcbi = amcbi_star.abs().multiply(-1).where(constraint, amcbi_star)

    return amcbi.rename("AMCBI")


def built_up_mask(amcbi: ee.Image) -> ee.Image:
    """Binary built-up classification: 1 where built-up, 0 otherwise."""
    return amcbi.gt(0).rename("built_up")
