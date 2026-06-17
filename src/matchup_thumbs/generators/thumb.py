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
from ..contrast import Treatment
from ._outline import _apply_outline
from .registry import register
from .types import DecodedAssets, TeamDict

# ---------------------------------------------------------------------------
# Canvas dimensions — D-01
# ---------------------------------------------------------------------------

_THUMB_W: int = 1280
_THUMB_H: int = 720

# ---------------------------------------------------------------------------
# Layout constants (Claude's discretion — see CONTEXT.md "Discretion" note)
# ---------------------------------------------------------------------------

_LOGO_SIZE: int = 280  # each logo resized to this square
_BLUR_RADIUS: int = 40  # GaussianBlur radius for diagonal seam blend
_VS_FONT_SIZE: int = 144  # BarlowCondensed-Bold pixel size for "VS"
_VS_STROKE_WIDTH: int = 5  # black outline around VS wordmark
_LEAGUE_LOGO_BOX: int = 160  # contain-fit bounding box for league logo (D-01/D-03)
_LEAGUE_LOGO_MARGIN: int = 32  # gap from bottom canvas edge to logo bottom (D-01/D-03)


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
    # Background filled from pre-computed ContrastDecision (D-02, CTR-01).
    # The render layer computed background_rgb upstream; generators stay pure (GEN-04).
    away_rgb = assets["away_decision"].background_rgb
    home_rgb = assets["home_decision"].background_rgb

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
    home_cx = 3 * _THUMB_W // 4
    logo_placements = [
        (assets["away_logo"], _THUMB_W // 4, _THUMB_H // 2, assets["away_decision"]),
        (assets["home_logo"], home_cx, _THUMB_H // 2, assets["home_decision"]),
    ]
    for logo_img, cx, cy, decision in logo_placements:
        # Defensive .convert("RGBA") handles RGB-mode ESPN logos (T-03-07 / Pitfall 2)
        logo_rgba = logo_img.convert("RGBA").resize(
            (_LOGO_SIZE, _LOGO_SIZE),
            Image.Resampling.LANCZOS,
        )
        # Apply OUTLINE halo when directed by the contrast decision (D-04, D-07).
        # Unconditional: drawn regardless of which variant loaded (D-04).
        if decision.treatment == Treatment.OUTLINE:
            logo_rgba = _apply_outline(logo_rgba, decision.background_rgb)
        bg.paste(logo_rgba, (cx - _LOGO_SIZE // 2, cy - _LOGO_SIZE // 2), logo_rgba)

    # --- League logo or VS wordmark fallback (D-09, BRAND-01/BRAND-04) ---
    if assets["league_logo"] is not None:
        # Aspect-preserving contain-fit (D-01).
        # thumbnail() mutates in-place — always copy first (Pitfall 3).
        league_logo_copy = assets["league_logo"].copy()
        league_logo_copy.thumbnail(
            (_LEAGUE_LOGO_BOX, _LEAGUE_LOGO_BOX), Image.Resampling.LANCZOS
        )
        lw, lh = league_logo_copy.size
        # Apply OUTLINE halo when directed by contrast decision (D-04, BRAND-03).
        # Read background_rgb from league_decision — not directly from seam (Pitfall 2).
        if (
            assets["league_decision"] is not None
            and assets["league_decision"].treatment == Treatment.OUTLINE
        ):
            league_logo_copy = _apply_outline(
                league_logo_copy, assets["league_decision"].background_rgb
            )
        # Bottom-center placement (D-01): horizontally centered, bottom-anchored.
        cx = _THUMB_W // 2
        cy = _THUMB_H - _LEAGUE_LOGO_MARGIN - lh // 2
        bg.paste(league_logo_copy, (cx - lw // 2, cy - lh // 2), league_logo_copy)
    else:
        # VS wordmark fallback — preserved as future ad-hoc-text seam (BRAND-04)
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
