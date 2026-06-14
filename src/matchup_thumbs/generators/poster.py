"""Poster generator (style=0) — 800×1200 vertical split.

Pure function: inputs in, PIL.Image out, no I/O (GEN-04).

Layout (D-04):
- Away team on top (upper 600px band) with primary colour and centred logo.
- Home team on bottom (lower 600px band) with primary colour and centred logo.
- White "VS" wordmark at the horizontal seam with a black stroke (D-08).

NULL colours fall back to named grey constants (D-15).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..assets import _load_font
from ._color import NULL_PRIMARY, hex_to_rgb
from .registry import register
from .types import DecodedAssets, TeamDict

# ---------------------------------------------------------------------------
# Canvas dimensions — D-01
# ---------------------------------------------------------------------------

_POSTER_W: int = 800
_POSTER_H: int = 1200

# ---------------------------------------------------------------------------
# Colour fallbacks — D-15 (imported from shared _color module)
# ---------------------------------------------------------------------------

_NULL_PRIMARY: tuple[int, int, int] = NULL_PRIMARY

# ---------------------------------------------------------------------------
# Layout constants (Claude's discretion — see CONTEXT.md "Discretion" note)
# ---------------------------------------------------------------------------

_BAND_H: int = _POSTER_H // 2  # 600px each band
_LOGO_SIZE: int = 320  # each logo resized to this square within its band
_VS_FONT_SIZE: int = 120  # BarlowCondensed-Bold pixel size for "VS"
_VS_STROKE_WIDTH: int = 5  # black outline around VS wordmark


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


@register("poster", 0)
def generate_poster_style0(
    away: TeamDict,
    home: TeamDict,
    assets: DecodedAssets,
) -> Image.Image:
    """Return an 800×1200 vertical-split poster with logos and VS wordmark.

    Away team occupies the upper band, home team the lower band (D-06).
    Pure — no I/O (GEN-04).  Runs in a threadpool via anyio.to_thread.run_sync
    so it never blocks the async event loop.
    """
    away_rgb = hex_to_rgb(away["primary_color"], _NULL_PRIMARY)
    home_rgb = hex_to_rgb(home["primary_color"], _NULL_PRIMARY)

    # --- Vertical split background ---
    bg = Image.new("RGB", (_POSTER_W, _POSTER_H), away_rgb)
    home_band = Image.new("RGB", (_POSTER_W, _BAND_H), home_rgb)
    bg.paste(home_band, (0, _BAND_H))

    # --- Logo placement ---
    # Away logo: centred in upper band  (D-06: away first/top)
    # Home logo: centred in lower band
    for logo_img, cy in [
        (assets["away_logo"], _BAND_H // 2),  # away — upper band centre
        (assets["home_logo"], _BAND_H + _BAND_H // 2),  # home — lower band centre
    ]:
        # Defensive .convert("RGBA") handles RGB-mode ESPN logos (T-03-07 / Pitfall 2)
        logo_rgba = logo_img.convert("RGBA").resize(
            (_LOGO_SIZE, _LOGO_SIZE),
            Image.Resampling.LANCZOS,
        )
        cx = _POSTER_W // 2
        bg.paste(logo_rgba, (cx - _LOGO_SIZE // 2, cy - _LOGO_SIZE // 2), logo_rgba)

    # --- "VS" wordmark at the horizontal seam (D-08: VS only, no team names) ---
    draw = ImageDraw.Draw(bg)
    font = _load_font(_VS_FONT_SIZE)
    draw.text(
        (_POSTER_W // 2, _BAND_H),
        "VS",
        fill="white",
        font=font,
        anchor="mm",
        stroke_width=_VS_STROKE_WIDTH,
        stroke_fill=(0, 0, 0),
    )

    return bg
