"""Generator unit and golden-image tests (GEN-01..GEN-05, GEN-07, D-15).

Covers:
- Dimension assertions for all three image kinds (thumb 1280×720, logo 800×800,
  poster 800×1200) — implemented in Plan 02.
- Registry lookup by (kind, style) and unknown-kind/style path — Plan 02.
- NULL color fallback to grey constants (D-15) — Plan 02.
- Golden-image regression tests — must run inside the production Docker image
  per GEN-06 (font anti-aliasing is architecture-specific).

Tests that target symbols implemented in Plan 02 are marked with
``pytest.mark.skip`` so this file collects without errors during Wave 0.
Plan 02 removes the skips as it implements each symbol.

Run inside Docker for golden tests:
    docker compose run --rm api pytest tests/test_generators.py \\
        --image-snapshot-update -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from tests.conftest import fixture_clippers, fixture_decoded_assets, fixture_lakers

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# ---------------------------------------------------------------------------
# GEN-01: thumb generates 1280×720 PIL.Image
# ---------------------------------------------------------------------------

_SKIP_PLAN02 = pytest.mark.skip(reason="Implemented in Plan 02")


@_SKIP_PLAN02
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


@_SKIP_PLAN02
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


@_SKIP_PLAN02
def test_poster_style0_dimensions() -> None:
    """Poster canvas is exactly 800×1200 (D-01, GEN-03)."""
    from matchup_thumbs.generators.poster import generate_poster_style0

    img = generate_poster_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    assert img.size == (800, 1200)


# ---------------------------------------------------------------------------
# GEN-05: Registry lookup by (kind, style)
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_registry_lookup() -> None:
    """get_generator returns a callable for each registered (kind, style) (GEN-05)."""
    from matchup_thumbs.generators import get_generator

    # All style=0 generators must be registered
    for kind in ("thumb", "logo", "poster"):
        gen_fn = get_generator(kind, 0)
        assert gen_fn is not None, f"Expected generator for ({kind!r}, 0)"
        assert callable(gen_fn)


# ---------------------------------------------------------------------------
# D-15: NULL color falls back to grey constants
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_null_color_fallback() -> None:
    """Generators render without error when team primary_color is None (D-15)."""
    from matchup_thumbs.generators.thumb import generate_thumb_style0

    no_color_lakers: dict[str, Any] = {**fixture_lakers(), "primary_color": None}
    no_color_clippers: dict[str, Any] = {**fixture_clippers(), "primary_color": None}

    # Must not raise; result must still be the correct canvas size
    img = generate_thumb_style0(
        no_color_lakers, no_color_clippers, fixture_decoded_assets()
    )
    assert img.size == (1280, 720)


# ---------------------------------------------------------------------------
# GEN-06: Golden-image regression (must run inside Docker image per GEN-06)
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
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


@_SKIP_PLAN02
def test_logo_style0_golden(image_snapshot: Any) -> None:  # type: ignore[misc]
    """Visual regression for logo style=0 Lakers vs Clippers (GEN-06)."""
    from matchup_thumbs.generators.logo import generate_logo_style0

    img: Image.Image = generate_logo_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    image_snapshot(img, SNAPSHOT_DIR / "logo_style0_lakers_clippers.png")


@_SKIP_PLAN02
def test_poster_style0_golden(image_snapshot: Any) -> None:  # type: ignore[misc]
    """Visual regression for poster style=0 Lakers vs Clippers (GEN-06)."""
    from matchup_thumbs.generators.poster import generate_poster_style0

    img: Image.Image = generate_poster_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    image_snapshot(img, SNAPSHOT_DIR / "poster_style0_lakers_clippers.png")
