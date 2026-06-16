"""Unit tests for load_league_logo (LGL-04, D-07, D-10).

Covers:
- Warm Redis key → load_league_logo returns a PIL.Image in RGBA mode
- Cold Redis key (None) → load_league_logo returns None
- Corrupted cached bytes → load_league_logo returns None (does NOT raise)

All tests are async (asyncio_mode=auto from pyproject.toml — no decorator needed).
No live ESPN, no network — Redis interaction is fully mocked via the conftest
mock_redis fixture.

Run: uv run pytest tests/test_league_logo_loader.py -x -q
"""

from __future__ import annotations

import io

from PIL import Image

from matchup_thumbs.assets.loader import (
    load_league_logo,  # noqa: F401 (RED: ImportError until 11-03)
)
from matchup_thumbs.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fixture_png() -> bytes:
    """Return valid PNG bytes for a small test image (50×50 solid red, RGBA).

    Creates the bytes in-memory — no filesystem read needed.  Deterministic
    output so tests remain reproducible regardless of Pillow version.
    """
    buf = io.BytesIO()
    Image.new("RGBA", (50, 50), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# LGL-04 loader tests
# ---------------------------------------------------------------------------


async def test_load_league_logo_warm_key_returns_image(mock_redis):  # type: ignore[no-untyped-def]
    """Warm Redis key → returns a decoded PIL.Image in RGBA mode (LGL-04)."""
    png_bytes = _make_fixture_png()
    mock_redis.get.return_value = png_bytes

    result = await load_league_logo(
        slug="nba",
        variant="default",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is not None, "Expected a PIL.Image on a warm Redis key, got None"
    assert isinstance(result, Image.Image), (
        f"Expected PIL.Image, got {type(result)}"
    )
    assert result.mode == "RGBA", (
        f"Expected RGBA mode, got {result.mode}"
    )


async def test_load_league_logo_cold_key_returns_none(mock_redis):  # type: ignore[no-untyped-def]
    """Cold Redis key (miss) → returns None without raising (LGL-04, D-07)."""
    mock_redis.get.return_value = None  # explicit miss

    result = await load_league_logo(
        slug="nba",
        variant="default",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is None, (
        f"Expected None on a cold Redis key, got {type(result)}"
    )


async def test_load_league_logo_corrupted_bytes_returns_none(mock_redis):  # type: ignore[no-untyped-def]
    """Corrupted cached bytes → returns None and does NOT raise (LGL-04).

    The degrade-don't-crash posture means corrupted entries become None so the
    render layer can fall back to the VS wordmark rather than crashing.
    """
    mock_redis.get.return_value = b"not a png"

    result = await load_league_logo(
        slug="nba",
        variant="default",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is None, (
        f"Expected None on corrupted bytes, got {type(result)} (must NOT raise)"
    )
