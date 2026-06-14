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
        f"Expected grey fallback {_NULL_PRIMARY!r} at (0,0), "
        f"got {top_left_pixel!r}"
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
