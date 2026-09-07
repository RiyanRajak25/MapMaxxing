"""Suppress false change caused by classification instability.

THE MEASURED PROBLEM

Comparing two composites of the SAME dry season over Bhopal - Nov-Dec 2023
against Jan-Feb 2024, where the true built-up change is zero by construction -
the pipeline reported 30.6 km2 of change and -6.2% "growth". Over the real
2017->2024 run it reported 12.6 km2 of built-up LOSS against a 63.2 km2 city:
20% of Bhopal demolished in seven years. That did not happen.

See scripts/diagnose_stability.py, which measures this without needing any
reference data, and scripts/evaluate_change_filters.py, which is where the
constant below comes from.

THE CAUSE

Built-up requires AMCBI* > 0 AND K_MIN <= k <= K_MAX. Both are hard thresholds
applied to two independently noisy composites. Real built-up sits only ~0.05-0.08
above K_MIN, and k = Blue / SWIR1 divides by the band carrying the largest
residual left over by atmospheric correction, at the low reflectance (~0.10)
where its relative error is worst. The noise is the same size as the margin, so
boundary pixels flip on measurement error alone. Measured over the real run:
71% of "lost" pixels failed on k, and 58% of lost area sat in patches of 5
pixels or fewer.

THE FIX

A minimum mapping unit on the change classes: a change patch smaller than
CHANGE_MIN_PIXELS is not reported as change. Applied symmetrically to gain and
loss so it cannot bias the direction.

Measured on the null test, an 11 px unit (1100 m2) cuts false change from 34.6%
of the built-up area to 9.8%, while signal-to-noise - real churn over null churn
- rises from 1.55 to 2.90. It is not free: it also discards genuine isolated
change, so a single new house on its own will no longer register. That is the
deliberate trade, and CHANGE_MIN_PIXELS tunes it.

WHAT THIS DOES NOT FIX

The net-change bias. Two halves of one dry season classify 88.6 km2 and 83.1 km2
of built-up - a -6.2% swing that no patch filter can touch, because it is a
shift in the classification LEVEL rather than speckle around its edges.
Per-scene majority voting was tested against it (scripts/evaluate_voting.py) and
made it worse, -10.3%, which is itself the evidence that the bias is systematic
seasonal drift rather than random noise: averaging more scenes measures a
systematic offset more precisely instead of cancelling it.

The mitigation for that one is procedural, not algorithmic - give both periods
the same calendar window, as scripts/run_bhopal.py does - and the residual has
to be reported as uncertainty rather than filtered away.
"""

import ee

from app.services import amcbi as amcbi_service
from app import config

# A change patch below this many pixels is treated as noise. At Sentinel-2's
# 10 m resolution, 11 px is 1100 m2. Chosen by measuring signal-to-noise against
# the same-season null test; see the module docstring.
CHANGE_MIN_PIXELS = config.CHANGE_MIN_PIXELS

# See hysteresis_change below.
CHANGE_CONFIDENCE_MARGIN = config.CHANGE_CONFIDENCE_MARGIN


def drop_small_patches(
    binary: ee.Image, projection: ee.Projection, min_pixels: int = CHANGE_MIN_PIXELS
) -> ee.Image:
    """Zero out connected patches of the 1-class smaller than min_pixels.

    `projection` must be the imagery's native grid. Connectivity runs in
    whatever projection the request carries, and the default is a 1-degree
    WGS84 grid, on which "11 pixels" would mean something entirely different.
    """
    if min_pixels <= 1:
        return binary

    # connectedPixelCount is evaluated on the 1-class only, so patch sizes are
    # measured within the change class rather than against the background.
    # Counting stops at min_pixels + 1, which is all the resolution needed here.
    sized = binary.selfMask().reproject(projection).connectedPixelCount(min_pixels + 1, True)
    return binary.And(sized.gte(min_pixels).unmask(0)).rename(binary.bandNames())


def filter_change(
    gain: ee.Image, loss: ee.Image, projection: ee.Projection,
    min_pixels: int = CHANGE_MIN_PIXELS,
) -> tuple[ee.Image, ee.Image]:
    """Apply the minimum mapping unit to both change classes symmetrically."""
    return (
        drop_small_patches(gain, projection, min_pixels),
        drop_small_patches(loss, projection, min_pixels),
    )


def min_change_unit_m2(scale: int, min_pixels: int = CHANGE_MIN_PIXELS) -> float:
    """The smallest change patch that will be reported, in square metres."""
    return float(min_pixels * scale * scale)


def hysteresis_change(
    star1: ee.Image,
    k1: ee.Image,
    star2: ee.Image,
    k2: ee.Image,
    margin: float = CHANGE_CONFIDENCE_MARGIN,
) -> tuple[ee.Image, ee.Image]:
    """Gain and loss requiring decisive evidence at both ends.

    WHY THE PLAIN COMPARISON IS NOT ENOUGH

    The minimum mapping unit removes scattered single pixels, but it cannot help
    a pixel that flips as part of a larger coherent patch. Measured on the loss
    that survives the MMU over Bhopal (scripts/diagnose_loss.py):

      - 59.3% still satisfy Eq. 1 in 2024 - they still look built-up spectrally,
        and only the k constraint rejected them
      - their median 2024 k is 0.391 against a K_MIN of 0.42, so the typical
        "demolished" pixel missed the bound by 0.029
      - 53.5% sit within 0.04 of the bound

    Those are standing buildings that fell a hair under a threshold.

    THE RULE

    Gain requires decisively non-built-up before AND decisively built-up after.
    Loss requires the reverse. A pixel hovering near a bound in either period
    satisfies neither and is reported as no change, which is the honest answer.
    Both classes use the same margin, so this cannot bias the direction.

    MEASURED EFFECT (Bhopal, with the 11 px MMU already applied)

        margin   real gain   real loss   null gain   null loss
        none         21.51        3.66        2.62        6.06
        0.02         11.61        1.64        1.23        1.98
        0.04          6.14        0.76        0.67        0.67

    True loss over Bhopal is ~0, so the real-loss column is almost entirely
    error, and the null columns should be 0 outright. At 0.02 known error falls
    5x while gain keeps just over half its recall - the knee of the curve, and
    the shipped default. 0.04 buys a little more precision for a lot more recall.

    WHAT IT COSTS

    Real recall. Genuine change that is spectrally marginal - a part-built plot,
    a thin roof over vegetation - stops being reported. gain_km2 will read well
    below net_change_km2 as a result; they measure different things, and only
    net_change_km2 should be read as the growth figure.
    """
    if margin <= 0:
        built1 = amcbi_service.built_up_with_margin(star1, k1, 0)
        built2 = amcbi_service.built_up_with_margin(star2, k2, 0)
        return built2.And(built1.Not()), built1.And(built2.Not())

    core1 = amcbi_service.built_up_with_margin(star1, k1, margin)
    core2 = amcbi_service.built_up_with_margin(star2, k2, margin)
    loose1 = amcbi_service.built_up_with_margin(star1, k1, -margin)
    loose2 = amcbi_service.built_up_with_margin(star2, k2, -margin)

    return core2.And(loose1.Not()), core1.And(loose2.Not())
