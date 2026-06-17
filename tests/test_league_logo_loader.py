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
    assert isinstance(result, Image.Image), f"Expected PIL.Image, got {type(result)}"
    assert result.mode == "RGBA", f"Expected RGBA mode, got {result.mode}"


async def test_load_league_logo_cold_key_returns_none(mock_redis):  # type: ignore[no-untyped-def]
    """Cold Redis key (miss) → returns None without raising (LGL-04, D-07)."""
    mock_redis.get.return_value = None  # explicit miss

    result = await load_league_logo(
        slug="nba",
        variant="default",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is None, f"Expected None on a cold Redis key, got {type(result)}"


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


# ---------------------------------------------------------------------------
# 12-04 fallback chain tests — Tests A, B, C
# ---------------------------------------------------------------------------


async def test_load_league_logo_cold_variant_warm_default_returns_image(mock_redis):  # type: ignore[no-untyped-def]
    """Test A (regression fix): cold :dark + warm :default → returns PIL.Image.

    This is the NCAA regression: ncaaf/ncaab seed warms :default (placeholder)
    but leaves :dark cold.  select_league_logo_variant returns "dark" for NCAA's
    dark-seam colours.  Before this fix, load_league_logo("ncaaf", "dark", ...)
    returned None and generators fell back to the VS wordmark.  After the fix,
    the loader falls back to :default and returns the placeholder image.

    Uses mock_redis.get.side_effect keyed on the exact bytes Redis key so the
    dark key misses and the default key hits.
    """
    png_bytes = _make_fixture_png()

    def _side_effect(key: bytes) -> bytes | None:
        if key == b"leaguelogo:ncaaf:dark":
            return None  # cold
        if key == b"leaguelogo:ncaaf:default":
            return png_bytes  # warm
        return None

    mock_redis.get.side_effect = _side_effect

    result = await load_league_logo(
        slug="ncaaf",
        variant="dark",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is not None, (
        "Expected a PIL.Image when :dark is cold but :default is warm (NCAA regression)"
    )
    assert isinstance(result, Image.Image), f"Expected PIL.Image, got {type(result)}"
    assert result.mode == "RGBA", f"Expected RGBA mode, got {result.mode}"


async def test_load_league_logo_no_variant_warmed_returns_none(mock_redis):  # type: ignore[no-untyped-def]
    """Test B: neither :dark nor :default warmed → returns None (VS-fallback signal).

    When no league-logo variant is warm in Redis, the loader exhausts all
    candidates without a successful decode and returns None so the render layer
    can take the VS-wordmark fallback path.
    """
    mock_redis.get.return_value = None  # all keys cold

    result = await load_league_logo(
        slug="ncaaf",
        variant="dark",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is None, (
        f"Expected None when no variant is warmed, got {type(result)}"
    )


async def test_load_league_logo_cold_default_request_returns_none(mock_redis):  # type: ignore[no-untyped-def]
    """Test C: variant="default" cold → returns None (no looping fallback).

    When the requested variant IS "default" there is no further candidate to try
    (deduplication: ["default"] candidate list has length 1).  The loader must
    return None rather than performing a second Redis read or entering an
    infinite loop.
    """
    mock_redis.get.return_value = None  # :default cold

    result = await load_league_logo(
        slug="ncaaf",
        variant="default",
        redis=mock_redis,
        settings=Settings(),
    )

    assert result is None, (
        f"Expected None when :default is cold (no further fallback), got {type(result)}"
    )
    # Exactly one Redis get call (the :default key only — no looping second read)
    assert mock_redis.get.call_count == 1, (
        f"Expected exactly 1 Redis get call for variant='default', "
        f"got {mock_redis.get.call_count}"
    )
