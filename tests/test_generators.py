"""Generator unit and golden-image tests (GEN-01..GEN-05, GEN-07, D-15).

Covers:
- Dimension assertions for all three image kinds (thumb 1280×720, logo 800×800,
  poster 800×1200) — GEN-01/GEN-02/GEN-03
- Registry lookup by (kind, style) and unknown-kind/style path — GEN-05/GEN-07
- NULL color fallback to grey constants (D-15)
- Golden-image regression tests — must run inside the production Docker image
  per GEN-06 (font anti-aliasing is architecture-specific).

Run inside Docker for golden tests:
    docker compose run --rm api pytest tests/test_generators.py \\
        --image-snapshot-update -q
Verify committed baselines:
    docker compose run --rm api pytest tests/test_generators.py \\
        --image-snapshot-fail-if-missing -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image, features

from tests.conftest import fixture_clippers, fixture_decoded_assets, fixture_lakers

# ---------------------------------------------------------------------------
# Synthetic helpers for contrast tests (D-11 — deterministic, no live ESPN)
# ---------------------------------------------------------------------------


def _make_solid_logo(
    rgb: tuple[int, int, int], size: tuple[int, int] = (100, 100)
) -> Image.Image:
    """Solid opaque RGBA logo with transparent background padding (D-11)."""
    canvas = Image.new("RGBA", (size[0] + 20, size[1] + 20), (0, 0, 0, 0))
    inner = Image.new("RGBA", size, rgb + (255,))
    canvas.paste(inner, (10, 10))
    return canvas


def _make_team(primary: str, secondary: str) -> dict[str, Any]:
    """Minimal TeamDict for a synthetic test team (D-11)."""
    return {
        "id": 99,
        "league_id": 1,
        "slug": "test-team",
        "display_name": "Test",
        "abbreviation": "TST",
        "primary_color": primary,
        "secondary_color": secondary,
        "logo_url": None,
        "espn_id": "99",
        "logo_variants": None,
    }

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Golden tests require the same FreeType text-rendering environment as the
# production Docker image (python:3.14-slim-bookworm without raqm/harfbuzz).
# Hosts with raqm installed produce different anti-aliasing for the "VS"
# wordmark.  Skip the golden tests on such hosts; they are always gated via
# the Docker run in CI (GEN-06, RESEARCH.md Pitfall 1).
_RAQM_AVAILABLE = features.check_feature("raqm")
_SKIP_GOLDEN = pytest.mark.skipif(
    _RAQM_AVAILABLE,
    reason=(
        "Host has raqm/harfbuzz which alters FreeType text anti-aliasing "
        "vs the production Docker image.  Run golden tests inside Docker: "
        "docker compose run --rm api pytest tests/test_generators.py "
        "--image-snapshot-fail-if-missing -q"
    ),
)


# ---------------------------------------------------------------------------
# GEN-01: thumb generates 1280×720 PIL.Image
# ---------------------------------------------------------------------------


def test_thumb_style0_dimensions() -> None:
    """Thumb canvas is exactly 1280×720 (D-01, GEN-01)."""
    from matchup_thumbs.generators.thumb import generate_thumb_style0

    img = generate_thumb_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    assert img.size == (1280, 720)


# ---------------------------------------------------------------------------
# GEN-02: logo generates 800×800 PIL.Image
# ---------------------------------------------------------------------------


def test_logo_style0_dimensions() -> None:
    """Logo canvas is exactly 800×800 (D-01, GEN-02)."""
    from matchup_thumbs.generators.logo import generate_logo_style0

    img = generate_logo_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    assert img.size == (800, 800)


# ---------------------------------------------------------------------------
# GEN-03: poster generates 800×1200 PIL.Image
# ---------------------------------------------------------------------------


def test_poster_style0_dimensions() -> None:
    """Poster canvas is exactly 800×1200 (D-01, GEN-03)."""
    from matchup_thumbs.generators.poster import generate_poster_style0

    img = generate_poster_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    assert img.size == (800, 1200)


# ---------------------------------------------------------------------------
# GEN-05 / GEN-07: Registry lookup by (kind, style)
# ---------------------------------------------------------------------------


def test_registry_lookup() -> None:
    """get_generator returns a callable for each registered (kind, style) (GEN-05).

    Also verifies that unknown kind and unknown style both return None,
    enabling the 400 path in the render pipeline (GEN-07).
    """
    from matchup_thumbs.generators import get_generator
    from matchup_thumbs.generators.logo import generate_logo_style0
    from matchup_thumbs.generators.poster import generate_poster_style0
    from matchup_thumbs.generators.thumb import generate_thumb_style0

    # All style=0 generators must be registered and return the correct function
    assert get_generator("thumb", 0) is generate_thumb_style0
    assert get_generator("logo", 0) is generate_logo_style0
    assert get_generator("poster", 0) is generate_poster_style0

    # Unknown kind → None (GEN-07: 400 path)
    assert get_generator("bogus", 0) is None

    # Unknown style → None (GEN-07: 400 path)
    assert get_generator("thumb", 9) is None


# ---------------------------------------------------------------------------
# D-15: NULL color falls back to grey constants
# ---------------------------------------------------------------------------


def test_null_color_fallback() -> None:
    """Generators render without error when team primary_color is None (D-15).

    When primary_color is None the generator must use the named grey fallback
    constants (_NULL_PRIMARY = #3A3A3A = (58, 58, 58)) rather than raise.
    The away-team region (upper-left before the diagonal seam) should be grey.
    """
    from matchup_thumbs.generators.thumb import _NULL_PRIMARY, generate_thumb_style0

    no_color_lakers: dict[str, Any] = {**fixture_lakers(), "primary_color": None}
    no_color_clippers: dict[str, Any] = {**fixture_clippers(), "primary_color": None}

    # Must not raise; result must still be the correct canvas size
    img = generate_thumb_style0(
        no_color_lakers, no_color_clippers, fixture_decoded_assets()
    )
    assert img.size == (1280, 720)

    # The top-left corner (away region, solidly in the away colour triangle
    # and far from the diagonal seam or logo) should be the grey fallback.
    # We sample the very first pixel which is always in the away colour band.
    top_left_pixel = img.getpixel((0, 0))
    assert top_left_pixel[:3] == _NULL_PRIMARY, (
        f"Expected grey fallback {_NULL_PRIMARY!r} at (0,0), got {top_left_pixel!r}"
    )


# ---------------------------------------------------------------------------
# CR-03: Malformed hex color strings fall back to grey (not ValueError)
# ---------------------------------------------------------------------------


def test_malformed_hex_color_fallback() -> None:
    """Generators must not raise when primary_color is a malformed hex string (CR-03).

    hex_to_rgb() in _color.py must return the D-15 grey fallback for any
    malformed or short hex string instead of propagating ValueError into the
    render threadpool.
    """
    from matchup_thumbs.generators._color import NULL_PRIMARY, hex_to_rgb

    fb = NULL_PRIMARY

    # None → fallback (pre-existing behaviour)
    assert hex_to_rgb(None, fb) == fb
    # Empty string → fallback
    assert hex_to_rgb("", fb) == fb
    # CSS 3-digit shorthand (#abc) → fallback (cannot expand to 6 digits safely)
    assert hex_to_rgb("#abc", fb) == fb
    # Bare hash → fallback
    assert hex_to_rgb("#", fb) == fb
    # Non-hex characters → fallback
    assert hex_to_rgb("#xyzxyz", fb) == fb
    # Valid 6-digit hex → parsed correctly
    assert hex_to_rgb("#552583", fb) == (85, 37, 131)
    assert hex_to_rgb("#3A3A3A", fb) == (58, 58, 58)


def test_malformed_hex_generators_do_not_raise() -> None:
    """Generators complete without raising for malformed primary_color (CR-03).

    Covers both thumb and poster generators since both use hex_to_rgb.
    """
    from matchup_thumbs.generators.poster import generate_poster_style0
    from matchup_thumbs.generators.thumb import generate_thumb_style0

    malformed_away: dict[str, Any] = {**fixture_lakers(), "primary_color": "#abc"}
    malformed_home: dict[str, Any] = {**fixture_clippers(), "primary_color": ""}

    # Must not raise; result must be the correct canvas size
    assets = fixture_decoded_assets()
    thumb_img = generate_thumb_style0(malformed_away, malformed_home, assets)
    assert thumb_img.size == (1280, 720)

    poster_img = generate_poster_style0(malformed_away, malformed_home, assets)
    assert poster_img.size == (800, 1200)


# ---------------------------------------------------------------------------
# Task 1: _apply_outline unit tests (D-07, D-08, CTR-04)
# ---------------------------------------------------------------------------


def test_apply_outline_preserves_size() -> None:
    """_apply_outline returns an image with the same dimensions as the input (D-07)."""
    from matchup_thumbs.generators._outline import _apply_outline

    logo = _make_solid_logo((100, 100, 200))  # opaque blue mark with transparent padding
    result = _apply_outline(logo, background_rgb=(100, 100, 200))
    assert result.size == logo.size


def test_apply_outline_halo_present() -> None:
    """_apply_outline makes previously-transparent border pixels opaque (halo ring) (D-07).

    A small solid mark placed in the center of a transparent canvas should gain
    a visible halo of opaque pixels around its original border after _apply_outline.
    """
    from matchup_thumbs.generators._outline import _apply_outline

    # Build a logo: 10x10 opaque mark, surrounded by transparent padding on a 30x30 canvas
    canvas = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
    inner = Image.new("RGBA", (10, 10), (200, 50, 50, 255))
    canvas.paste(inner, (10, 10))

    result = _apply_outline(canvas, background_rgb=(200, 50, 50))
    result_pixels = list(result.getdata())

    # The corner pixel (0,0) is far from the mark; it may or may not be halo depending
    # on radius. But a pixel adjacent to the mark border (e.g., (9,9)) should be opaque
    # after dilation with _OUTLINE_DILATION_RADIUS >= 1.
    adjacent_pixel = result.getpixel((9, 9))  # type: ignore[assignment]
    assert adjacent_pixel[3] > 0, (
        "Expected pixel adjacent to mark to be opaque after halo dilation"
    )


def test_apply_outline_halo_color_dark_background() -> None:
    """On a dark (near-black) background, _apply_outline picks white halo (D-08)."""
    from matchup_thumbs.generators._outline import _apply_outline

    # Near-black background → white has higher contrast than black
    dark_bg: tuple[int, int, int] = (10, 10, 10)
    logo = _make_solid_logo((255, 255, 255))  # white mark (clearly different)
    # Place mark in center to ensure adjacent pixels get halo
    result = _apply_outline(logo, background_rgb=dark_bg)

    # Find an opaque pixel that is NOT part of the original mark (the halo ring).
    # The original mark occupies (10..109, 10..109) in a 120x120 canvas (per _make_solid_logo).
    # Check pixel (9, 9) — one pixel outside the mark; after dilation it should be white (255,255,255).
    halo_pixel = result.getpixel((9, 9))  # type: ignore[assignment]
    if halo_pixel[3] > 0:  # only check color if the pixel is actually in the halo
        r, g, b = halo_pixel[0], halo_pixel[1], halo_pixel[2]
        assert r > 128, f"Expected white halo on dark background, got r={r}"


def test_apply_outline_halo_color_light_background() -> None:
    """On a near-white background, _apply_outline picks black halo (D-08)."""
    from matchup_thumbs.generators._outline import _apply_outline

    # Near-white background → black has higher contrast than white
    light_bg: tuple[int, int, int] = (245, 245, 245)
    logo = _make_solid_logo((0, 0, 0))  # black mark
    result = _apply_outline(logo, background_rgb=light_bg)

    # Check adjacent pixel after dilation — should be dark (halo is black)
    halo_pixel = result.getpixel((9, 9))  # type: ignore[assignment]
    if halo_pixel[3] > 0:
        r, g, b = halo_pixel[0], halo_pixel[1], halo_pixel[2]
        assert r < 128, f"Expected black halo on light background, got r={r}"


# ---------------------------------------------------------------------------
# CTR-01: Crimson-on-crimson repro — logo must be discernible (D-11)
# ---------------------------------------------------------------------------


def test_crimson_on_crimson_repro_is_discernible() -> None:
    """CTR-01 repro: crimson logo on crimson background must become discernible.

    Asserts the ContrastDecision swaps background OR applies OUTLINE.
    Never asserts a specific pixel color (fragile); asserts the decision action.
    Uses deterministic synthetic fixtures — no live ESPN call (D-11).
    """
    from matchup_thumbs.contrast import Treatment, decide_contrast, dominant_color
    from matchup_thumbs.generators._color import (
        NULL_PRIMARY,
        NULL_SECONDARY,
        hex_to_rgb,
    )

    crimson_hex = "#9E1B32"  # Alabama crimson
    navy_hex = "#14213D"  # contrasting secondary
    logo = _make_solid_logo((158, 27, 50))  # crimson logo pixels

    primary_rgb = hex_to_rgb(crimson_hex, NULL_PRIMARY)
    secondary_rgb = hex_to_rgb(navy_hex, NULL_SECONDARY)
    repr_rgb = dominant_color(logo)
    decision = decide_contrast(primary_rgb, secondary_rgb, repr_rgb, None)

    # Background must differ from primary OR OUTLINE must be applied
    if decision.background_rgb == primary_rgb:
        assert decision.treatment == Treatment.OUTLINE, (
            "Crimson logo on crimson background must trigger OUTLINE when"
            " background stays crimson"
        )
    # else: background swapped to secondary — discernibility via color swap; test passes


# ---------------------------------------------------------------------------
# TEST-01: Synthetic worst-case — logo color equals background (D-11)
# ---------------------------------------------------------------------------


def test_logo_color_equals_background_treatment_required() -> None:
    """Synthetic worst case: logo dominant color == both team colors → OUTLINE required.

    When both primary and secondary have 1.0 contrast ratio against the logo
    representative color (identical colors), the engine must emit OUTLINE.
    TEST-01, D-11.
    """
    from matchup_thumbs.contrast import SelectionReason, Treatment, decide_contrast

    crimson: tuple[int, int, int] = (158, 27, 50)
    decision = decide_contrast(
        primary_rgb=crimson,
        secondary_rgb=crimson,
        repr_rgb=crimson,
        logo_variants=None,
    )
    assert decision.treatment == Treatment.OUTLINE
    assert decision.reason == SelectionReason.TREATMENT_REQUIRED
    assert decision.achieved_ratio == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# GEN-06: Golden-image regression (must run inside Docker image per GEN-06)
# ---------------------------------------------------------------------------


@_SKIP_GOLDEN
def test_thumb_style0_golden(image_snapshot: Any) -> None:  # type: ignore[misc]
    """Visual regression for thumb style=0 Lakers vs Clippers.

    Must run inside the production Docker image to produce deterministic
    FreeType output (GEN-06).  Generate baselines with --image-snapshot-update.
    """
    from matchup_thumbs.generators.thumb import generate_thumb_style0

    img: Image.Image = generate_thumb_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    image_snapshot(img, SNAPSHOT_DIR / "thumb_style0_lakers_clippers.png")


@_SKIP_GOLDEN
def test_logo_style0_golden(image_snapshot: Any) -> None:  # type: ignore[misc]
    """Visual regression for logo style=0 Lakers vs Clippers (GEN-06)."""
    from matchup_thumbs.generators.logo import generate_logo_style0

    img: Image.Image = generate_logo_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    image_snapshot(img, SNAPSHOT_DIR / "logo_style0_lakers_clippers.png")


@_SKIP_GOLDEN
def test_poster_style0_golden(image_snapshot: Any) -> None:  # type: ignore[misc]
    """Visual regression for poster style=0 Lakers vs Clippers (GEN-06)."""
    from matchup_thumbs.generators.poster import generate_poster_style0

    img: Image.Image = generate_poster_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    image_snapshot(img, SNAPSHOT_DIR / "poster_style0_lakers_clippers.png")
