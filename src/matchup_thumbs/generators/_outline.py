"""OUTLINE treatment rendering for matchup-thumbs generators.

Pure function — no I/O, no randomness.  Produces a contrasting dilated halo
behind an RGBA logo image to ensure discernibility against a solid background.
Used by thumb.py and poster.py when ContrastDecision.treatment == Treatment.OUTLINE
(D-07, CTR-04).
"""

from __future__ import annotations

from PIL import Image, ImageFilter

from ..contrast import contrast_ratio

# ---------------------------------------------------------------------------
# Named constants (AGENTS.md: no magic numbers in logic)
# ---------------------------------------------------------------------------

# MaxFilter window = 2*R+1 = 9px; produces a ~4px stroke around the logo mark
_OUTLINE_DILATION_RADIUS: int = 4

# Pure white and black for outline color selection (D-08)
_WHITE: tuple[int, int, int] = (255, 255, 255)
_BLACK: tuple[int, int, int] = (0, 0, 0)


def _apply_outline(
    logo_rgba: Image.Image,
    background_rgb: tuple[int, int, int],
) -> Image.Image:
    """Return logo_rgba with a contrasting dilated halo composited underneath.

    Halo color is whichever of pure white or pure black yields higher contrast
    ratio against background_rgb (D-08).  Dilation uses MaxFilter on the alpha
    channel only (Pillow C-path — not a per-pixel Python loop; T-03-06 pattern).

    The returned image has the same dimensions as the input.  Transparent
    exterior pixels remain transparent; a halo ring of opaque outline_color
    surrounds the original mark; original logo pixels are composited on top.

    Args:
        logo_rgba:       RGBA logo image (transparent background around the mark).
        background_rgb:  The chosen background color for this team.

    Returns:
        RGBA image: transparent exterior → halo ring → original logo pixels.
        Pass as both image and mask to bg.paste().
    """
    # Pick halo color by contrast ratio against the background (D-08).
    # White wins on ties (>= comparison).
    white_ratio = contrast_ratio(_WHITE, background_rgb)
    black_ratio = contrast_ratio(_BLACK, background_rgb)
    outline_color: tuple[int, int, int] = (
        _WHITE if white_ratio >= black_ratio else _BLACK
    )

    # Dilate the alpha channel by _OUTLINE_DILATION_RADIUS pixels.
    # MaxFilter on the L-mode alpha channel only (NOT the full RGBA).
    # Filtering RGB channels would bleed color into the halo — avoid that.
    # Self-guard: callers pass RGBA, but convert defensively so a non-RGBA
    # image (RGB/L) cannot raise IndexError on .split()[3] (WR-03). Idempotent.
    alpha = logo_rgba.convert("RGBA").split()[3]
    window = _OUTLINE_DILATION_RADIUS * 2 + 1
    dilated_alpha = alpha.filter(ImageFilter.MaxFilter(window))

    # Build solid-color halo layer shaped by the dilated alpha mask.
    # Image.composite(img1, img2, mask): white mask → img1, black mask → img2.
    halo = Image.new("RGBA", logo_rgba.size, outline_color + (255,))
    transparent = Image.new("RGBA", logo_rgba.size, (0, 0, 0, 0))
    halo_masked = Image.composite(halo, transparent, dilated_alpha)

    # Composite: halo behind the original logo (logo pixels win on overlap).
    return Image.alpha_composite(halo_masked, logo_rgba)
