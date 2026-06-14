"""Thumbnail generator (style=0) — 1280×720 diagonal two-colour split.

Pure function: inputs in, PIL.Image out, no I/O (GEN-04).

Layout (D-03):
- Away team primary colour fills the upper-left triangle.
- Home team primary colour fills the lower-right triangle.
- Diagonal seam blended with GaussianBlur (Pillow C-path — not a per-pixel
  Python loop; resolves T-03-06 DoS risk from a CPU-exhausting loop).
- Away team logo centred in the left/upper quadrant (D-06: away first).
- Home team logo centred in the right/lower quadrant.
- White "VS" wordmark at the centre crossing with a black stroke (D-08).

NULL colours fall back to named grey constants (D-15).
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from ..assets import _load_font
from ._color import NULL_PRIMARY, hex_to_rgb
from .registry import register
from .types import DecodedAssets, TeamDict

# ---------------------------------------------------------------------------
# Canvas dimensions — D-01
# ---------------------------------------------------------------------------

_THUMB_W: int = 1280
_THUMB_H: int = 720

# ---------------------------------------------------------------------------
# Colour fallbacks — D-15 (imported from shared _color module)
# ---------------------------------------------------------------------------

_NULL_PRIMARY: tuple[int, int, int] = NULL_PRIMARY

# ---------------------------------------------------------------------------
# Layout constants (Claude's discretion — see CONTEXT.md "Discretion" note)
# ---------------------------------------------------------------------------

_LOGO_SIZE: int = 280  # each logo resized to this square
_BLUR_RADIUS: int = 40  # GaussianBlur radius for diagonal seam blend
_VS_FONT_SIZE: int = 144  # BarlowCondensed-Bold pixel size for "VS"
_VS_STROKE_WIDTH: int = 5  # black outline around VS wordmark


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


@register("thumb", 0)
def generate_thumb_style0(
    away: TeamDict,
    home: TeamDict,
    assets: DecodedAssets,
) -> Image.Image:
    """Return a 1280×720 diagonal two-colour split with logos and VS wordmark.

    Pure — no I/O (GEN-04).  Runs in a threadpool via anyio.to_thread.run_sync
    so it never blocks the async event loop.
    """
    away_rgb = hex_to_rgb(away["primary_color"], _NULL_PRIMARY)
    home_rgb = hex_to_rgb(home["primary_color"], _NULL_PRIMARY)

    # --- Diagonal split background via GaussianBlur mask (T-03-06 mitigation) ---
    # Image.composite(overlay, base, mask) blends home colour over away colour
    # in the lower-right triangle defined by the polygon mask.
    base = Image.new("RGB", (_THUMB_W, _THUMB_H), away_rgb)
    overlay = Image.new("RGB", (_THUMB_W, _THUMB_H), home_rgb)
    mask = Image.new("L", (_THUMB_W, _THUMB_H), 0)
    ImageDraw.Draw(mask).polygon(
        [(_THUMB_W, 0), (_THUMB_W, _THUMB_H), (0, _THUMB_H)],
        fill=255,
    )
    soft_mask = mask.filter(ImageFilter.GaussianBlur(radius=_BLUR_RADIUS))
    bg = Image.composite(overlay, base, soft_mask)

    # --- Logo placement ---
    # Away logo: centred at (W/4, H/2)  — left quadrant (D-06: away first/left)
    # Home logo: centred at (3W/4, H/2) — right quadrant
    for logo_img, cx, cy in [
        (assets["away_logo"], _THUMB_W // 4, _THUMB_H // 2),
        (assets["home_logo"], 3 * _THUMB_W // 4, _THUMB_H // 2),
    ]:
        # Defensive .convert("RGBA") handles RGB-mode ESPN logos (T-03-07 / Pitfall 2)
        logo_rgba = logo_img.convert("RGBA").resize(
            (_LOGO_SIZE, _LOGO_SIZE),
            Image.Resampling.LANCZOS,
        )
        bg.paste(logo_rgba, (cx - _LOGO_SIZE // 2, cy - _LOGO_SIZE // 2), logo_rgba)

    # --- "VS" wordmark (D-08: VS only, no team names) ---
    draw = ImageDraw.Draw(bg)
    font = _load_font(_VS_FONT_SIZE)
    draw.text(
        (_THUMB_W // 2, _THUMB_H // 2),
        "VS",
        fill="white",
        font=font,
        anchor="mm",
        stroke_width=_VS_STROKE_WIDTH,
        stroke_fill=(0, 0, 0),
    )

    return bg
