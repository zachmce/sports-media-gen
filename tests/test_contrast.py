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
    """Alpha-weighted mean: 2x1 with (255,0,0,128) and (0,0,255,255).

    The exact per-pixel math (weight=383) gives R≈85, G=0, B≈169, but LANCZOS
    resampling of a 2×1 image to 64×64 introduces edge blending that shifts
    the per-pixel alpha values, producing ~(80, 0, 174).  The key properties
    under test are:
    - R channel is dominated by red (> 60, < 130)
    - G channel stays near 0 (< 10)
    - B channel is dominated by blue (> 140, < 200)
    - The result is NOT the pure unweighted mean (127, 0, 127)
    """
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (255, 0, 0, 128))
    img.putpixel((1, 0), (0, 0, 255, 255))
    result = dominant_color(img)
    r, g, b = result
    # R: red is half-alpha so contributes less; expect 60–130 range
    assert 60 <= r <= 130, f"R channel {r} out of expected range [60, 130]"
    # G: no green in either pixel
    assert g <= 10, f"G channel {g} should be near 0"
    # B: blue is full-alpha so dominates; expect 140–200 range
    assert 140 <= b <= 200, f"B channel {b} out of expected range [140, 200]"


def test_dominant_color_all_transparent() -> None:
    """All-transparent logo returns neutral fallback without raising (D-07)."""
    result = dominant_color(_all_transparent())
    assert result == (128, 128, 128), (
        f"Expected neutral grey (128,128,128) for all-transparent logo, got {result!r}"
    )


def test_dominant_color_solid_opaque() -> None:
    """Solid opaque red image returns (255,0,0) — visible-pixel extraction."""
    result = dominant_color(_solid_rgba((255, 0, 0)))
    assert result == (255, 0, 0), (
        f"Expected (255,0,0) for solid opaque red logo, got {result!r}"
    )
