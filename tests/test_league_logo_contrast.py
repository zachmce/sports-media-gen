"""Unit tests for select_league_logo_variant (TEST-04, D-09, D-10).

Covers:
- Dark seam + logo_variants has "dark" → returns "dark"
- Light seam + logo_variants has "dark" → returns "default"
- Any seam + logo_variants missing "dark" → returns "default"
- Any seam + logo_variants is None → returns "default"
- Luminance boundary: seam at relative_luminance >= 0.5 → "default"
  (the gate is luminance < 0.5, so values AT or ABOVE 0.5 are "light" and
   must return "default")

All tests are synchronous (pure function — no async needed).
No live ESPN, no network — inputs are synthetic RGB tuples only (D-10).

Run: uv run pytest tests/test_league_logo_contrast.py -x -q
"""

from __future__ import annotations

import pytest

from matchup_thumbs.contrast import (  # noqa: F401 (RED: ImportError until 11-03)
    relative_luminance,
    select_league_logo_variant,
)

# ---------------------------------------------------------------------------
# Representative variant maps
# ---------------------------------------------------------------------------

_VARIANTS_WITH_DARK: dict[str, str] = {
    "default": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
    "dark": "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500-dark/nba.png",
}

_VARIANTS_NO_DARK: dict[str, str] = {
    "default": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
}


# ---------------------------------------------------------------------------
# TEST-04 variant-selection tests
# ---------------------------------------------------------------------------


def test_dark_seam_with_dark_variant_returns_dark() -> None:
    """Dark seam (0,0,0) + variants has 'dark' → returns 'dark' (TEST-04)."""
    result = select_league_logo_variant((0, 0, 0), _VARIANTS_WITH_DARK)
    assert result == "dark", (
        f"Expected 'dark' for a black seam with dark variant available, got '{result}'"
    )


def test_light_seam_with_dark_variant_returns_default() -> None:
    """Light seam (255,255,255) + variants has 'dark' → returns 'default' (TEST-04).

    A white seam has luminance=1.0, which is >= 0.5 → the dark (white) logo
    would be invisible on a white background.  Must return 'default'.
    """
    result = select_league_logo_variant((255, 255, 255), _VARIANTS_WITH_DARK)
    assert result == "default", (
        f"Expected 'default' for a white seam (luminance=1.0), got '{result}'"
    )


def test_any_seam_missing_dark_key_returns_default() -> None:
    """Any seam + variants missing 'dark' key → 'default' (TEST-04)."""
    # Dark seam, but variant map has no 'dark' key
    result = select_league_logo_variant((0, 0, 0), _VARIANTS_NO_DARK)
    assert result == "default", (
        f"Expected 'default' when 'dark' key is absent from variants, got '{result}'"
    )


def test_any_seam_variants_none_returns_default() -> None:
    """Any seam + logo_variants is None → 'default' (D-10; NCAA placeholder)."""
    result = select_league_logo_variant((100, 100, 100), None)
    assert result == "default", (
        f"Expected 'default' when logo_variants is None, got '{result}'"
    )


def test_luminance_boundary_at_or_above_threshold_returns_default() -> None:
    """Seam at relative_luminance >= 0.5 → returns 'default' (gate is < not <=).

    RGB (188, 188, 188) has relative_luminance ≈ 0.503 — just above the 0.5
    threshold — so the dark variant is NOT selected.  Confirms the gate is a
    strict < comparison (values AT 0.5 are treated as "light" and return "default").
    """
    seam_rgb = (188, 188, 188)
    seam_luminance = relative_luminance(seam_rgb)
    # Verify the test anchor: luminance must be >= 0.5 (otherwise this test is wrong)
    assert seam_luminance == pytest.approx(0.502, abs=0.01), (
        f"Test anchor failure: expected luminance near 0.502, got {seam_luminance:.4f}"
    )
    result = select_league_logo_variant(seam_rgb, _VARIANTS_WITH_DARK)
    assert result == "default", (
        f"Expected 'default' at luminance {seam_luminance:.4f} (>= 0.5), got '{result}'"
    )
