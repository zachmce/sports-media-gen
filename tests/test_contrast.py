"""Contrast engine unit tests (CTR-03, CTR-04, TEST-02).

Covers:
- WCAG relative_luminance anchors: white=1.0, black=0.0
- contrast_ratio anchor: white-on-black = 21:1 (W3C reference pair)
- Same-color identity: contrast_ratio(c, c) == 1.0
- dominant_color: alpha-weighted mean, all-transparent fallback
- dominant_color: visible-pixel exclusion (solid opaque)

All tests are synchronous (pure functions — no async needed).
Run: uv run pytest tests/test_contrast.py -x -q
"""

from __future__ import annotations

import pytest
from PIL import Image

from matchup_thumbs.contrast import (
    contrast_ratio,
    dominant_color,
    relative_luminance,
)

# ---------------------------------------------------------------------------
# CTR-03: WCAG luminance and contrast ratio anchors
# ---------------------------------------------------------------------------


def test_relative_luminance_anchors() -> None:
    """white=1.0, black=0.0 — W3C reference values (TEST-02)."""
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0, abs=1e-4)
    assert relative_luminance((0, 0, 0)) == pytest.approx(0.0, abs=1e-4)


def test_contrast_ratio_white_black() -> None:
    """white-on-black = 21:1 — W3C reference pair (CTR-03, TEST-02)."""
    ratio = contrast_ratio((255, 255, 255), (0, 0, 0))
    assert ratio == pytest.approx(21.0, abs=1e-2)


def test_contrast_ratio_same_color_identity() -> None:
    """Identical colors produce 1:1 ratio (minimum possible contrast)."""
    ratio = contrast_ratio((158, 27, 50), (158, 27, 50))
    assert ratio == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# TEST-02: alpha-aware dominant-color extraction
# ---------------------------------------------------------------------------


def _solid_rgba(
    rgb: tuple[int, int, int],
    alpha: int = 255,
    size: tuple[int, int] = (100, 100),
) -> Image.Image:
    """Solid RGBA image — quick fixture for dominant_color() tests."""
    r, g, b = rgb
    return Image.new("RGBA", size, (r, g, b, alpha))


def _all_transparent() -> Image.Image:
    """All-transparent RGBA image — degenerate input for D-07 fallback."""
    return Image.new("RGBA", (10, 10), (255, 0, 0, 0))  # alpha=0 everywhere


def test_dominant_color_alpha_weighted() -> None:
    """Alpha-weighted mean: 2x1 with (255,0,0,128) and (0,0,255,255) → ≈(85,0,169).

    Verified by hand:
    - weight_red = 128, weight_blue = 255, total = 383
    - R = (255*128 + 0*255) // 383 = 32640 // 383 = 85
    - G = 0
    - B = (0*128 + 255*255) // 383 = 65025 // 383 = 169
    """
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (255, 0, 0, 128))
    img.putpixel((1, 0), (0, 0, 255, 255))
    result = dominant_color(img)
    # After 64x64 downscale via LANCZOS the single-row 2x1 image becomes uniform
    # enough that channels are close to the weighted mean; allow ±2 tolerance.
    r, g, b = result
    assert abs(r - 85) <= 2, f"R channel {r} not within ±2 of expected 85"
    assert abs(g - 0) <= 2, f"G channel {g} not within ±2 of expected 0"
    assert abs(b - 169) <= 2, f"B channel {b} not within ±2 of expected 169"


def test_dominant_color_all_transparent() -> None:
    """All-transparent image returns neutral fallback (128,128,128) without raising (D-07)."""
    result = dominant_color(_all_transparent())
    assert result == (128, 128, 128), (
        f"Expected neutral grey fallback (128,128,128) for all-transparent logo, got {result!r}"
    )


def test_dominant_color_solid_opaque() -> None:
    """Solid opaque red image returns (255,0,0) — visible-pixel extraction."""
    result = dominant_color(_solid_rgba((255, 0, 0)))
    assert result == (255, 0, 0), (
        f"Expected (255,0,0) for solid opaque red logo, got {result!r}"
    )
