"""Logo generator (style=0) — 800×800 side-by-side logos on neutral background.

Pure function: inputs in, PIL.Image out, no I/O (GEN-04).

Layout (D-05):
- Neutral dark background (no colour split, no VS text).
- Away logo centred in the left half (D-06: away first/left).
- Home logo centred in the right half.

This is the "paired logo" form — deliberately minimal.
NULL colours do not affect this generator; it ignores team colours entirely.
"""

from __future__ import annotations

from PIL import Image

from .registry import register
from .types import DecodedAssets, TeamDict

# ---------------------------------------------------------------------------
# Canvas dimensions — D-01
# ---------------------------------------------------------------------------

_LOGO_W: int = 800
_LOGO_H: int = 800

# ---------------------------------------------------------------------------
# Visual constants (Claude's discretion — see CONTEXT.md "Discretion" note)
# ---------------------------------------------------------------------------

_NEUTRAL_BG: tuple[int, int, int] = (40, 40, 40)  # dark grey neutral background
_PANEL_LOGO_SIZE: int = 320  # each logo resized to this square (fits in 400px half)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


@register("logo", 0)
def generate_logo_style0(
    away: TeamDict,
    home: TeamDict,
    assets: DecodedAssets,
) -> Image.Image:
    """Return an 800×800 side-by-side logo image on a neutral dark background.

    Pure — no I/O (GEN-04).  Runs in a threadpool via anyio.to_thread.run_sync
    so it never blocks the async event loop.
    """
    bg = Image.new("RGB", (_LOGO_W, _LOGO_H), _NEUTRAL_BG)

    # Half-width panel centres for away (left) and home (right)
    half_w = _LOGO_W // 2  # 400

    for logo_img, panel_cx, cy in [
        (assets["away_logo"], half_w // 2, _LOGO_H // 2),  # away left
        (assets["home_logo"], half_w + half_w // 2, _LOGO_H // 2),  # home right
    ]:
        # Defensive .convert("RGBA") handles RGB-mode ESPN logos (T-03-07 / Pitfall 2)
        logo_rgba = logo_img.convert("RGBA").resize(
            (_PANEL_LOGO_SIZE, _PANEL_LOGO_SIZE),
            Image.Resampling.LANCZOS,
        )
        bg.paste(
            logo_rgba,
            (panel_cx - _PANEL_LOGO_SIZE // 2, cy - _PANEL_LOGO_SIZE // 2),
            logo_rgba,
        )

    return bg
