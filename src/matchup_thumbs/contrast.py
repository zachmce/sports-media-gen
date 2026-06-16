"""WCAG contrast decision engine for matchup-thumbs.

Pure functions — no I/O, no randomness.  Decides which team background color
and logo variant achieve sufficient visual contrast for a given logo color.
Implements CTR-03 (WCAG luminance / contrast-ratio math) and TEST-02
(alpha-aware dominant-color extraction).  The decision entry point
``decide_contrast`` is added in plan 09-02.
"""

from __future__ import annotations

from typing import cast

from PIL import Image

# ---------------------------------------------------------------------------
# WCAG 2.x SC 1.4.11 Non-text Contrast threshold (D-04)
# Reference: https://www.w3.org/TR/WCAG21/#non-text-contrast
# 3:1 applies to graphical objects/logos; 4.5:1 is for body text (AA).
# ---------------------------------------------------------------------------

MIN_CONTRAST_RATIO: float = 3.0  # 3:1 for graphical objects/logos (D-04)

# ---------------------------------------------------------------------------
# sRGB linearization constants (W3C WCAG 2.1 relative-luminance formula)
# Reference: https://www.w3.org/TR/WCAG21/relative-luminance.html
# Note: the correct threshold is 0.04045 (updated May 2021); some older
# references still show 0.03928 — do not use 0.03928.
# ---------------------------------------------------------------------------

# Piecewise linearization parameters
_SRGB_THRESHOLD: float = 0.04045  # sRGB linearization threshold (WCAG 2.1, NOT 0.03928)
_SRGB_LINEAR_DIVISOR: float = 12.92  # low-value linear scale
_SRGB_GAMMA_OFFSET: float = 0.055  # gamma formula offset
_SRGB_GAMMA_SCALE: float = 1.055  # gamma formula denominator divisor
_SRGB_GAMMA_EXP: float = 2.4  # sRGB effective gamma exponent

# ITU-R BT.709 luminance coefficients
_LUM_RED: float = 0.2126
_LUM_GREEN: float = 0.7152
_LUM_BLUE: float = 0.0722

# WCAG contrast ratio offset (added to both luminances before dividing)
_CONTRAST_OFFSET: float = 0.05

# ---------------------------------------------------------------------------
# Dominant-color extraction constants (D-06, D-07)
# ---------------------------------------------------------------------------

_DOMINANT_COLOR_SIZE: int = 64  # downscale target W × H (D-06)
_FALLBACK_REPR_COLOR: tuple[int, int, int] = (128, 128, 128)  # neutral mid-grey (D-07)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _linearize(c8: int) -> float:
    """Convert an 8-bit sRGB channel value [0..255] to a linear light value.

    Applies the piecewise sRGB transfer function defined in W3C WCAG 2.1
    ``relative-luminance.html``.  Uses threshold 0.04045 (updated May 2021).
    """
    s = c8 / 255.0
    if s <= _SRGB_THRESHOLD:
        return s / _SRGB_LINEAR_DIVISOR
    return float(((s + _SRGB_GAMMA_OFFSET) / _SRGB_GAMMA_SCALE) ** _SRGB_GAMMA_EXP)


# ---------------------------------------------------------------------------
# Public WCAG math primitives (CTR-03)
# ---------------------------------------------------------------------------


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Return the W3C WCAG 2.1 relative luminance of an sRGB colour.

    Formula: L = 0.2126*R_lin + 0.7152*G_lin + 0.0722*B_lin
    where each channel is first linearized via ``_linearize``.

    Reference: https://www.w3.org/TR/WCAG21/relative-luminance.html

    Returns:
        float in [0.0, 1.0]; white → 1.0, black → 0.0.
    """
    r, g, b = rgb
    return (
        _LUM_RED * _linearize(r)
        + _LUM_GREEN * _linearize(g)
        + _LUM_BLUE * _linearize(b)
    )


def contrast_ratio(
    rgb_a: tuple[int, int, int],
    rgb_b: tuple[int, int, int],
) -> float:
    """Return the WCAG 2.1 contrast ratio between two sRGB colours.

    Formula: (L_lighter + 0.05) / (L_darker + 0.05)
    Range: 1.0 (identical colours) to 21.0 (black vs white).

    Reference: https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio

    Args:
        rgb_a: First colour as an (R, G, B) 3-tuple of 8-bit integers.
        rgb_b: Second colour as an (R, G, B) 3-tuple of 8-bit integers.

    Returns:
        float ≥ 1.0.  White-on-black (or black-on-white) returns 21.0.
    """
    l_a = relative_luminance(rgb_a)
    l_b = relative_luminance(rgb_b)
    lighter = max(l_a, l_b)
    darker = min(l_a, l_b)
    return (lighter + _CONTRAST_OFFSET) / (darker + _CONTRAST_OFFSET)


# ---------------------------------------------------------------------------
# Alpha-weighted dominant-color extraction (TEST-02, D-06, D-07)
# ---------------------------------------------------------------------------


def dominant_color(rgba_image: Image.Image) -> tuple[int, int, int]:
    """Return the alpha-weighted mean colour of visible pixels in *rgba_image*.

    D-06: The image is first downscaled to
    ``_DOMINANT_COLOR_SIZE × _DOMINANT_COLOR_SIZE`` (LANCZOS resampling) for
    determinism and speed — caps pixel iteration at
    4096 pixels regardless of source logo dimensions.  Fully-transparent pixels
    (``alpha == 0``) are excluded entirely; remaining pixels are weighted by
    their alpha value so semi-transparent edges contribute proportionally.

    D-07: Degenerate-input contract — an all-transparent image (or any image
    where every pixel has ``alpha == 0``) returns the neutral mid-grey fallback
    ``(128, 128, 128)`` without raising.  This makes the function a total
    function over any valid ``PIL.Image`` input (degrade-don't-crash posture).

    Args:
        rgba_image: A ``PIL.Image`` in any mode; converted to RGBA defensively.
                    The loader guarantees RGBA from Phase 8 onward, but the
                    defensive convert ensures correctness for any caller.

    Returns:
        An (R, G, B) 3-tuple of 8-bit integers representing the dominant colour,
        or ``(128, 128, 128)`` for an all-transparent logo.
    """
    small = rgba_image.convert("RGBA").resize(
        (_DOMINANT_COLOR_SIZE, _DOMINANT_COLOR_SIZE),
        Image.Resampling.LANCZOS,
    )
    # Single cast at the boundary — accurate because .convert("RGBA") guarantees
    # RGBA mode and get_flattened_data() returns 4-tuples in that mode.
    # get_flattened_data() is the non-deprecated successor to getdata() in Pillow 12.2+.
    pixels = cast(
        list[tuple[int, int, int, int]],
        list(small.get_flattened_data()),
    )

    r_acc = g_acc = b_acc = weight = 0
    for r, g, b, a in pixels:
        if a == 0:
            continue  # exclude fully-transparent pixels (D-06)
        r_acc += r * a
        g_acc += g * a
        b_acc += b * a
        weight += a

    # Guard BEFORE division — D-07 total-function contract (no ZeroDivisionError).
    if weight == 0:
        return _FALLBACK_REPR_COLOR

    # Integer floor division: channels must be 8-bit ints (RESEARCH Pitfall 3).
    return (r_acc // weight, g_acc // weight, b_acc // weight)


