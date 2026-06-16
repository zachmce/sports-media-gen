"""Contrast engine unit tests (CTR-03, CTR-04, TEST-02).

Covers:
- WCAG relative_luminance anchors: white=1.0, black=0.0
- contrast_ratio anchor: white-on-black = 21:1 (W3C reference pair)
- Same-color identity: contrast_ratio(c, c) == 1.0
- dominant_color: alpha-weighted mean, all-transparent fallback
- dominant_color: visible-pixel exclusion (solid opaque)
- decide_contrast: primary_ok, swapped_to_secondary, treatment_required paths
- decide_contrast: Treatment never NONE below threshold (CTR-04 guard)
- ContrastDecision: frozen + achieved_ratio field naming
- Variant key recommendation when logo_variants is populated / None

All tests are synchronous (pure functions — no async needed).
Run: uv run pytest tests/test_contrast.py -x -q
"""

from __future__ import annotations

import dataclasses

import pytest
from PIL import Image

from matchup_thumbs.contrast import (
    SelectionReason,
    Treatment,
    contrast_ratio,
    decide_contrast,
    dominant_color,
    relative_luminance,
)
from matchup_thumbs.settings import Settings

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


# ---------------------------------------------------------------------------
# CTR-03: background selection — decide_contrast primary/secondary paths
# ---------------------------------------------------------------------------

# Verified reference pairs from 09-RESEARCH.md:
# Lakers purple #552583 (85,37,131) vs Lakers gold #fdb927 (253,185,39) → 6.138:1
# Clippers red #c8102e (200,16,46) vs Clippers blue #1d428a (29,66,138) → 1.624:1

_LAKERS_PURPLE: tuple[int, int, int] = (85, 37, 131)
_LAKERS_GOLD: tuple[int, int, int] = (253, 185, 39)
_CLIPPERS_RED: tuple[int, int, int] = (200, 16, 46)
_CLIPPERS_BLUE: tuple[int, int, int] = (29, 66, 138)
_WHITE: tuple[int, int, int] = (255, 255, 255)
_BLACK: tuple[int, int, int] = (0, 0, 0)


def test_decide_primary_ok() -> None:
    """When primary_ratio >= threshold, returns primary background with NONE treatment.

    White logo on Lakers purple: ratio > 6:1 → PRIMARY_OK (CTR-03).
    """
    result = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,
        logo_variants=None,
    )
    assert result.background_source == "primary", (
        f"Expected background_source='primary', got {result.background_source!r}"
    )
    assert result.treatment == Treatment.NONE, (
        f"Expected Treatment.NONE, got {result.treatment!r}"
    )
    assert result.reason == SelectionReason.PRIMARY_OK, (
        f"Expected SelectionReason.PRIMARY_OK, got {result.reason!r}"
    )
    assert result.background_rgb == _LAKERS_PURPLE, (
        f"Expected background_rgb={_LAKERS_PURPLE!r}, got {result.background_rgb!r}"
    )
    # achieved_ratio must match primary ratio (white-on-purple ≈ 6.138:1)
    primary_ratio = contrast_ratio(_WHITE, _LAKERS_PURPLE)
    assert result.achieved_ratio == pytest.approx(primary_ratio, abs=1e-4), (
        f"Expected achieved_ratio≈{primary_ratio:.4f}, got {result.achieved_ratio:.4f}"
    )


def test_decide_swapped_to_secondary() -> None:
    """When primary fails but secondary clears threshold, swaps to secondary (CTR-03).

    Black logo on Lakers gold: gold primary ≈ 6.1:1 (clears), but use a repr_rgb
    that makes primary fail to demonstrate the swap path.

    Use a white repr color against a low-contrast primary (Lakers gold → white
    is only 1.73:1) and a high-contrast secondary (Lakers purple → white is 6.1:1).
    Swap: primary=gold, secondary=purple, repr=white
    → gold-white ≈ 1.73:1 (fails 3.0), purple-white ≈ 6.1:1 (passes 3.0) → SWAPPED
    """
    result = decide_contrast(
        primary_rgb=_LAKERS_GOLD,  # gold vs white ≈ 1.73:1 → fails
        secondary_rgb=_LAKERS_PURPLE,  # purple vs white ≈ 6.1:1 → passes
        repr_rgb=_WHITE,
        logo_variants=None,
    )
    assert result.background_source == "secondary", (
        f"Expected background_source='secondary', got {result.background_source!r}"
    )
    assert result.treatment == Treatment.NONE, (
        f"Expected Treatment.NONE, got {result.treatment!r}"
    )
    assert result.reason == SelectionReason.SWAPPED_TO_SECONDARY, (
        f"Expected SelectionReason.SWAPPED_TO_SECONDARY, got {result.reason!r}"
    )
    assert result.background_rgb == _LAKERS_PURPLE, (
        f"Expected background_rgb={_LAKERS_PURPLE!r}, got {result.background_rgb!r}"
    )
    secondary_ratio = contrast_ratio(_WHITE, _LAKERS_PURPLE)
    assert result.achieved_ratio == pytest.approx(secondary_ratio, abs=1e-4), (
        f"Expected achieved_ratio≈{secondary_ratio:.4f}, "
        f"got {result.achieved_ratio:.4f}"
    )


# ---------------------------------------------------------------------------
# CTR-04: treatment fallback — OUTLINE when neither color clears threshold
# ---------------------------------------------------------------------------


def test_treatment_required_when_both_low() -> None:
    """When neither primary nor secondary clears threshold, returns OUTLINE.

    Clippers red vs blue → 1.624:1 (both below 3.0).
    Use a repr_rgb matching Clippers red so both ratios stay low.
    The engine must pick the higher-contrast of the two and return OUTLINE.
    """
    result = decide_contrast(
        primary_rgb=_CLIPPERS_RED,
        secondary_rgb=_CLIPPERS_BLUE,
        repr_rgb=_CLIPPERS_RED,  # same as primary → ratio=1.0; blue is still ~1.624:1
        logo_variants=None,
    )
    assert result.treatment == Treatment.OUTLINE, (
        f"Expected Treatment.OUTLINE when both ratios below threshold, "
        f"got {result.treatment!r} (reason={result.reason!r})"
    )
    assert result.reason == SelectionReason.TREATMENT_REQUIRED, (
        f"Expected SelectionReason.TREATMENT_REQUIRED, got {result.reason!r}"
    )
    # background should be the higher-contrast (blue>red when repr=red)
    expected_bg = _CLIPPERS_BLUE  # 1.624:1 > 1.0:1 (same-color)
    assert result.background_rgb == expected_bg, (
        f"Expected higher-contrast background {expected_bg!r}, "
        f"got {result.background_rgb!r}"
    )


def test_never_none_below_threshold() -> None:
    """CTR-04 guard: treatment must never be NONE when both ratios are below threshold.

    Clippers red vs blue → 1.624:1 (both saturated, mid-range).
    Use repr_rgb == Clippers red so primary ratio = 1.0 and
    secondary ratio ≈ 1.624 — both below the 3.0 threshold.
    Treatment.NONE below threshold is the silent-pass bug CTR-04 fixes.
    """
    # Verified reference pair from RESEARCH.md: Clippers red vs blue → 1.624:1
    # repr_rgb == primary → primary ratio = 1.0; secondary ratio ≈ 1.624:1
    repr_rgb = _CLIPPERS_RED

    # Verify the test precondition: both ratios truly below 3.0
    r_primary = contrast_ratio(repr_rgb, _CLIPPERS_RED)
    r_secondary = contrast_ratio(repr_rgb, _CLIPPERS_BLUE)
    assert max(r_primary, r_secondary) < 3.0, (
        f"Test precondition failed: expected both ratios < 3.0, "
        f"got primary={r_primary:.3f}, secondary={r_secondary:.3f}"
    )

    result = decide_contrast(
        primary_rgb=_CLIPPERS_RED,
        secondary_rgb=_CLIPPERS_BLUE,
        repr_rgb=repr_rgb,
        logo_variants=None,
    )
    max_ratio = max(r_primary, r_secondary)
    assert result.treatment != Treatment.NONE, (
        f"CTR-04 violated: got Treatment.NONE when max ratio {max_ratio:.3f} "
        f"is below threshold — engine must never silently pass low contrast"
    )
    assert result.treatment == Treatment.OUTLINE, (
        f"Expected Treatment.OUTLINE as last-resort (D-10), got {result.treatment!r}"
    )


def test_contrast_decision_frozen() -> None:
    """ContrastDecision is frozen — mutation raises FrozenInstanceError."""
    decision = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,
        logo_variants=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.treatment = Treatment.OUTLINE  # type: ignore[misc]


def test_contrast_decision_has_achieved_ratio_field() -> None:
    """ContrastDecision exposes achieved_ratio (not contrast_ratio) — Pitfall 5."""
    decision = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,
        logo_variants=None,
    )
    # Field must exist and be named achieved_ratio
    assert hasattr(decision, "achieved_ratio"), (
        "ContrastDecision must have field 'achieved_ratio'"
    )
    assert not hasattr(decision, "contrast_ratio"), (
        "ContrastDecision must NOT have field 'contrast_ratio' "
        "— name collision with function"
    )
    assert isinstance(decision.achieved_ratio, float), (
        f"achieved_ratio must be float, got {type(decision.achieved_ratio)!r}"
    )


# ---------------------------------------------------------------------------
# TEST-02: variant recommendation (Task 2 — stubs for RED phase)
# ---------------------------------------------------------------------------


def test_decide_recommends_variant() -> None:
    """primary bg + 'primary_logo_on_primary_color' in logo_variants → recommend it."""
    logo_variants = {
        "default": "https://example.com/default.png",
        "primary_logo_on_primary_color": "https://example.com/on_primary.png",
        "dark": "https://example.com/dark.png",
    }
    result = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,  # white vs purple → 6.1:1 → primary chosen
        logo_variants=logo_variants,
    )
    assert result.background_source == "primary", (
        f"Expected primary background, got {result.background_source!r}"
    )
    assert result.recommended_variant == "primary_logo_on_primary_color", (
        f"Expected 'primary_logo_on_primary_color', got {result.recommended_variant!r}"
    )


def test_decide_no_variants() -> None:
    """When logo_variants is None, recommended_variant is None."""
    result = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,
        logo_variants=None,
    )
    assert result.recommended_variant is None, (
        f"Expected recommended_variant=None when logo_variants=None, "
        f"got {result.recommended_variant!r}"
    )


def test_decide_no_variants_empty_dict() -> None:
    """When logo_variants is empty dict, recommended_variant is None."""
    result = decide_contrast(
        primary_rgb=_LAKERS_PURPLE,
        secondary_rgb=_LAKERS_GOLD,
        repr_rgb=_WHITE,
        logo_variants={},
    )
    assert result.recommended_variant is None, (
        f"Expected recommended_variant=None when logo_variants={{}}, "
        f"got {result.recommended_variant!r}"
    )


def test_decide_secondary_recommends_dark_variant() -> None:
    """When secondary bg chosen AND 'dark' in logo_variants → recommend 'dark'."""
    logo_variants = {
        "default": "https://example.com/default.png",
        "dark": "https://example.com/dark.png",
    }
    # gold primary vs white → 1.73:1 fails; purple secondary vs white → 6.1:1 passes
    result = decide_contrast(
        primary_rgb=_LAKERS_GOLD,
        secondary_rgb=_LAKERS_PURPLE,
        repr_rgb=_WHITE,
        logo_variants=logo_variants,
    )
    assert result.background_source == "secondary", (
        f"Expected secondary background, got {result.background_source!r}"
    )
    assert result.recommended_variant == "dark", (
        f"Expected recommended_variant='dark' for secondary bg, "
        f"got {result.recommended_variant!r}"
    )


# ---------------------------------------------------------------------------
# D-05: settings.min_contrast_ratio default (engine purity check)
# ---------------------------------------------------------------------------


def test_settings_min_contrast_ratio_default() -> None:
    """settings.min_contrast_ratio defaults to 3.0 (WCAG SC 1.4.11, D-04/D-05)."""
    s = Settings()
    assert s.min_contrast_ratio == 3.0, (
        f"Expected Settings.min_contrast_ratio=3.0, got {s.min_contrast_ratio!r}"
    )


# ---------------------------------------------------------------------------
# Vibrant strategy (v1.2.1): prefer the primary background + a contrasting
# logo variant before swapping to the secondary.
# ---------------------------------------------------------------------------


def test_decide_crimson_primary_keeps_primary_with_white_variant() -> None:
    """Crimson team (Alabama/Indiana) → KEEP crimson primary + white 'dark' variant.

    The default (crimson) logo clashes with the crimson primary, but a white
    ("dark") variant exists and contrasts crimson well (~5.9:1).  The vibrant
    strategy keeps the on-brand crimson background and recolours the logo white,
    rather than swapping to the white secondary (which produced a washed-out,
    all-white region in the previous behaviour).

    Scenario (the Alabama/Indiana case):
    - primary_rgb: crimson #9E1B32 (== logo repr → ratio 1.0 → default logo fails)
    - secondary_rgb: white #ffffff
    - logo_variants: contains "dark" (the white ESPN variant)
    - Expected: primary background, recommended_variant == "dark",
      reason == PRIMARY_LIGHT_VARIANT, and the white-on-crimson ratio clears 3.0
      (so the logo is NOT invisible).
    """
    _CRIMSON: tuple[int, int, int] = (158, 27, 50)
    _CRIMSON_PRIMARY: tuple[int, int, int] = (158, 27, 50)
    _WHITE_SECONDARY: tuple[int, int, int] = (255, 255, 255)

    logo_variants = {
        "default": "https://example.com/default.png",
        "dark": "https://example.com/dark.png",
    }

    result = decide_contrast(
        primary_rgb=_CRIMSON_PRIMARY,
        secondary_rgb=_WHITE_SECONDARY,
        repr_rgb=_CRIMSON,
        logo_variants=logo_variants,
    )

    assert result.background_source == "primary", (
        f"Expected primary background (vibrant), got {result.background_source!r}"
    )
    assert result.background_rgb == _CRIMSON_PRIMARY
    assert result.recommended_variant == "dark", (
        f"Expected the white 'dark' variant on the crimson primary, "
        f"got {result.recommended_variant!r}"
    )
    assert result.reason == SelectionReason.PRIMARY_LIGHT_VARIANT, (
        f"Expected PRIMARY_LIGHT_VARIANT, got {result.reason!r}"
    )
    assert result.treatment == Treatment.NONE
    # White-on-crimson must clear the threshold — the whole point is legibility.
    assert result.achieved_ratio == pytest.approx(
        contrast_ratio((255, 255, 255), _CRIMSON_PRIMARY), abs=1e-4
    )
    assert result.achieved_ratio >= 3.0


def test_decide_no_white_variant_still_swaps_to_light_secondary() -> None:
    """Without a 'dark' variant, a crimson team falls back to the secondary swap.

    When no white variant exists, branch 2 cannot fire, so the engine swaps to
    the white secondary and uses the default (crimson) logo — which contrasts
    white.  recommended_variant stays None (no variant to recommend).
    """
    _CRIMSON: tuple[int, int, int] = (158, 27, 50)
    _WHITE_SECONDARY: tuple[int, int, int] = (255, 255, 255)

    result = decide_contrast(
        primary_rgb=_CRIMSON,
        secondary_rgb=_WHITE_SECONDARY,
        repr_rgb=_CRIMSON,
        logo_variants={"default": "https://example.com/default.png"},  # no "dark"
    )

    assert result.background_source == "secondary"
    assert result.background_rgb == _WHITE_SECONDARY
    assert result.recommended_variant is None
    assert result.reason == SelectionReason.SWAPPED_TO_SECONDARY


# ---------------------------------------------------------------------------
# Hotfix v1.2.1 (prong 1): _recommend_variant gate must stay luminance-aware —
# the white 'dark' variant must never be recommended onto a LIGHT secondary
# background (the path that produced the white-on-white invisible logo).
# ---------------------------------------------------------------------------


def test_recommend_variant_skips_dark_on_light_secondary() -> None:
    """_recommend_variant returns None for the 'dark' key on a LIGHT background."""
    from matchup_thumbs.contrast import _recommend_variant

    logo_variants = {
        "default": "https://example.com/default.png",
        "dark": "https://example.com/dark.png",
    }
    # Secondary swap onto a light (white) background: 'dark' (white logo) would be
    # invisible → must NOT be recommended.
    assert _recommend_variant(logo_variants, "secondary", (255, 255, 255)) is None
    # Secondary swap onto a DARK background: 'dark' (white logo) contrasts → ok.
    assert _recommend_variant(logo_variants, "secondary", (20, 20, 20)) == "dark"
