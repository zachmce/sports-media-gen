"""Logo separation treatment for matchup-thumbs generators.

Pure function — no I/O, no randomness.  Produces a soft, contrast-adaptive
**drop shadow** behind an RGBA logo so the mark stays discernible against a
solid background (the game-thumbs look — user-approved 2026-06-18, replacing
the v1.2 hard dilated halo).  Used by thumb.py and poster.py when
ContrastDecision.treatment == Treatment.OUTLINE (D-07, CTR-04).

The shadow COLOR is still chosen adaptively (pure white or pure black, whichever
contrasts more with the background) so dark-logo-on-dark-bg cases get a light
glow and light-on-light cases get a dark shadow — preserving the v1.2 anti-swallow
guarantee.  Only the *shape* changed: a blurred, offset, semi-transparent shadow
instead of a tight opaque ring.
"""

from __future__ import annotations

from PIL import Image, ImageFilter

from ..contrast import contrast_ratio

# ---------------------------------------------------------------------------
# Named constants (AGENTS.md: no magic numbers in logic)
# ---------------------------------------------------------------------------

# Gaussian blur radius for the shadow softness (px).
_SHADOW_BLUR_RADIUS: int = 7
# Directional offset of the shadow from the mark (px, down-right) — reads as a
# drop shadow rather than a symmetric glow.  Kept small so it stays within the
# logo's transparent margin (no clipping for normally-padded marks).
_SHADOW_OFFSET: int = 5
# Shadow opacity (0.0–1.0) applied to the blurred shadow alpha.
_SHADOW_OPACITY: float = 0.6

# Pure white and black for adaptive shadow color selection (D-08)
_WHITE: tuple[int, int, int] = (255, 255, 255)
_BLACK: tuple[int, int, int] = (0, 0, 0)


def _apply_outline(
    logo_rgba: Image.Image,
    background_rgb: tuple[int, int, int],
) -> Image.Image:
    """Return logo_rgba with a soft contrast-adaptive drop shadow underneath.

    Shadow color is whichever of pure white or pure black yields the higher
    contrast ratio against background_rgb (D-08; white wins ties).  The shadow is
    the logo's alpha shape, offset by ``_SHADOW_OFFSET`` px, Gaussian-blurred by
    ``_SHADOW_BLUR_RADIUS`` and scaled to ``_SHADOW_OPACITY`` — a soft drop shadow
    rather than the former hard dilated ring.

    The returned image has the same dimensions as the input.  Pass it as both
    image and mask to ``bg.paste()`` (call sites unchanged).

    Args:
        logo_rgba:       RGBA logo image (transparent background around the mark).
        background_rgb:  The chosen background color for this team.

    Returns:
        RGBA image: transparent exterior → soft shadow → original logo pixels.
    """
    # Pick shadow color by contrast ratio against the background (D-08).
    # White wins on ties (>= comparison).
    white_ratio = contrast_ratio(_WHITE, background_rgb)
    black_ratio = contrast_ratio(_BLACK, background_rgb)
    shadow_color: tuple[int, int, int] = (
        _WHITE if white_ratio >= black_ratio else _BLACK
    )

    rgba = logo_rgba.convert("RGBA")
    alpha = rgba.split()[3]

    # Build the shadow as a SOLID shadow_color layer whose ALPHA carries the soft
    # offset+blurred logo shape.  Keeping RGB uniform (not compositing a coloured
    # shape over a black-RGB transparent canvas) avoids GaussianBlur muddying a
    # white glow's edges toward grey — only the alpha is blurred (Pillow C-path).
    mask = Image.new("L", rgba.size, 0)
    mask.paste(alpha, (_SHADOW_OFFSET, _SHADOW_OFFSET))  # offset down-right
    mask = mask.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR_RADIUS))
    mask = mask.point(lambda a: int(a * _SHADOW_OPACITY))  # scale to opacity

    shadow = Image.new("RGBA", rgba.size, shadow_color + (0,))
    shadow.putalpha(mask)

    # Composite: soft shadow behind the original logo (logo pixels win on overlap).
    return Image.alpha_composite(shadow, rgba)
