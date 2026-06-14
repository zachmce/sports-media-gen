"""Render pipeline tests (CACHE-01..05, OUT-01..03, GEN-04, GEN-07, D-14, D-16).

Unit tests use mock_redis (from conftest.py) and synthetic PNG fixtures.
No live services required for the unit suite.

Tests that target symbols implemented in Plans 02/04 are marked with
``pytest.mark.skip`` so this file collects without errors during Wave 0.
Plans 02/04 remove the skips as they implement each symbol.

Quick run:
    uv run pytest tests/test_render.py -x -q
Full suite:
    uv run pytest -q
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from tests.conftest import fixture_clippers, fixture_lakers

# ---------------------------------------------------------------------------
# Helper — synthetic PNG bytes
# ---------------------------------------------------------------------------

_SKIP_PLAN02 = pytest.mark.skip(reason="Implemented in Plan 02/04")


def _make_synthetic_png(size: tuple[int, int] = (100, 100)) -> bytes:
    """Return PNG bytes for a solid-grey image of the given size."""
    buf = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GEN-07: Unknown kind/style returns 400 (raises ValueError / KeyError)
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_unknown_kind_raises() -> None:
    """render_pipeline raises on an unknown kind/style combination (GEN-07)."""
    from unittest.mock import MagicMock

    from matchup_thumbs.render import render_pipeline

    from matchup_thumbs.settings import settings

    mock_redis = MagicMock()
    mock_http = MagicMock()

    with pytest.raises((ValueError, KeyError, LookupError)):
        await render_pipeline(
            league="nba",
            away=fixture_lakers(),
            home=fixture_clippers(),
            kind="unknown_kind",
            style=0,
            redis=mock_redis,
            http_client=mock_http,
            settings=settings,
        )


# ---------------------------------------------------------------------------
# GEN-04: Generator has no I/O; runs in threadpool
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_generator_is_pure() -> None:
    """Generator function completes via threadpool without I/O (GEN-04)."""
    from matchup_thumbs.generators.thumb import generate_thumb_style0
    from tests.conftest import fixture_decoded_assets

    # Pure function: call directly (no async, no I/O expected)
    img = generate_thumb_style0(
        fixture_lakers(), fixture_clippers(), fixture_decoded_assets()
    )
    assert img.size == (1280, 720)


# ---------------------------------------------------------------------------
# OUT-01: WebP response bytes decodable
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_webp_output() -> None:
    """post_cache_transform returns decodable WebP bytes for fmt='webp' (OUT-01)."""
    from matchup_thumbs.render import post_cache_transform

    png = _make_synthetic_png((100, 100))
    webp_bytes, content_type = post_cache_transform(
        png, kind="thumb", fmt="webp", requested_w=None
    )

    assert content_type == "image/webp"
    img = Image.open(io.BytesIO(webp_bytes))
    assert img.format == "WEBP"


# ---------------------------------------------------------------------------
# OUT-02: Width clamp produces correct dimensions
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_width_clamp() -> None:
    """post_cache_transform clamps width down only; never upscales (D-02, OUT-02)."""
    from matchup_thumbs.render import post_cache_transform

    # Native 200×100 PNG; clamp to 100px wide → 100×50
    png = _make_synthetic_png((200, 100))
    result_bytes, _ = post_cache_transform(
        png, kind="thumb", fmt="png", requested_w=100
    )
    img = Image.open(io.BytesIO(result_bytes))
    assert img.width == 100
    assert img.height == 50

    # Request wider than native → no upscale (stays at 200)
    result_bytes2, _ = post_cache_transform(
        png, kind="thumb", fmt="png", requested_w=400
    )
    img2 = Image.open(io.BytesIO(result_bytes2))
    assert img2.width == 200


# ---------------------------------------------------------------------------
# OUT-03: fmt/w not in render cache key
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_cache_key_excludes_fmt_w() -> None:
    """render_pipeline cache key does not include fmt or w parameters (OUT-03)."""
    from matchup_thumbs.render import _build_render_key

    from matchup_thumbs.settings import settings

    key = _build_render_key(
        league="nba",
        away=fixture_lakers(),
        home=fixture_clippers(),
        kind="thumb",
        style=0,
        settings=settings,
    )
    assert b"fmt" not in key
    assert b"webp" not in key
    assert b"png" not in key


# ---------------------------------------------------------------------------
# CACHE-01: Render bytes written to Redis with TTL
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_render_writes_cache(mock_redis: object) -> None:
    """render_pipeline writes PNG bytes to Redis with render_cache_ttl (CACHE-01)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline

    from matchup_thumbs.settings import settings

    # Simulate: cache miss → lock acquired → render → cache write
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)  # lock acquired
    redis.delete = AsyncMock()

    http_client = MagicMock()

    await render_pipeline(
        league="nba",
        away=fixture_lakers(),
        home=fixture_clippers(),
        kind="thumb",
        style=0,
        redis=redis,
        http_client=http_client,
        settings=settings,
    )

    # Verify cache write occurred with TTL
    ttl = settings.render_cache_ttl
    render_write_calls = [
        call
        for call in redis.set.call_args_list
        if call.kwargs.get("ex") == ttl
    ]
    assert len(render_write_calls) >= 1


# ---------------------------------------------------------------------------
# CACHE-02/03: Cache key includes render_version; bump → new key
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
def test_render_key_versioning() -> None:
    """Bumping render_version produces a different cache key (CACHE-02/03)."""
    from unittest.mock import MagicMock

    from matchup_thumbs.render import _build_render_key

    from matchup_thumbs.settings import Settings

    s1 = MagicMock(spec=Settings)
    s1.render_version = 1
    s2 = MagicMock(spec=Settings)
    s2.render_version = 2

    key1 = _build_render_key(
        "nba", fixture_lakers(), fixture_clippers(), "thumb", 0, s1
    )
    key2 = _build_render_key(
        "nba", fixture_lakers(), fixture_clippers(), "thumb", 0, s2
    )

    assert key1 != key2
    assert b"v1" in key1
    assert b"v2" in key2


# ---------------------------------------------------------------------------
# CACHE-04: Cache hit returns cached bytes without re-rendering
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_cache_hit_no_rerender() -> None:
    """Cache hit returns cached bytes; generator is not called (CACHE-04)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline

    from matchup_thumbs.settings import settings

    png_bytes = _make_synthetic_png()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=png_bytes)  # cache hit
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    http_client = MagicMock()

    result = await render_pipeline(
        league="nba",
        away=fixture_lakers(),
        home=fixture_clippers(),
        kind="thumb",
        style=0,
        redis=redis,
        http_client=http_client,
        settings=settings,
    )

    assert result == png_bytes
    redis.set.assert_not_called()  # no write on cache hit


# ---------------------------------------------------------------------------
# CACHE-04: Singleflight — waiter gets result from holder
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_singleflight_waiter() -> None:
    """Singleflight waiter polls and returns the holder's cached result (CACHE-04)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline

    from matchup_thumbs.settings import Settings

    png_bytes = _make_synthetic_png()

    # First get() → miss; second get() → result appears (simulates holder writing)
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=[None, None, png_bytes])
    redis.set = AsyncMock(return_value=None)  # lock NOT acquired (another holder)
    redis.delete = AsyncMock()

    mock_settings = MagicMock(spec=Settings)
    mock_settings.render_version = 1
    mock_settings.sf_lock_ttl = 10
    mock_settings.sf_poll_interval = 0.001  # fast polling in test
    mock_settings.sf_max_wait = 1.0
    mock_settings.render_cache_ttl = 60

    http_client = MagicMock()

    result = await render_pipeline(
        league="nba",
        away=fixture_lakers(),
        home=fixture_clippers(),
        kind="thumb",
        style=0,
        redis=redis,
        http_client=http_client,
        settings=mock_settings,
    )

    assert result == png_bytes


# ---------------------------------------------------------------------------
# CACHE-04 / D-14: Singleflight degrade — waiter renders locally on timeout
# ---------------------------------------------------------------------------


@_SKIP_PLAN02
async def test_singleflight_degrade() -> None:
    """Degraded fallback: waiter renders locally when max_wait elapses (D-14)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline

    from matchup_thumbs.settings import Settings

    # Redis never returns a result — waiter should fall through to local render
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)  # lock not acquired
    redis.delete = AsyncMock()

    mock_settings = MagicMock(spec=Settings)
    mock_settings.render_version = 1
    mock_settings.sf_lock_ttl = 10
    mock_settings.sf_poll_interval = 0.001
    # very short so degraded path triggers fast
    mock_settings.sf_max_wait = 0.005
    mock_settings.render_cache_ttl = 60
    mock_settings.logo_cache_ttl = 60

    http_client = MagicMock()

    # Degraded render completes without raising
    result = await render_pipeline(
        league="nba",
        away=fixture_lakers(),
        home=fixture_clippers(),
        kind="thumb",
        style=0,
        redis=redis,
        http_client=http_client,
        settings=mock_settings,
    )

    assert isinstance(result, bytes)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# D-16: Asset loader falls back to placeholder on Redis miss
# ---------------------------------------------------------------------------


async def test_asset_loader_fallback() -> None:
    """Asset loader returns placeholder when Redis misses and httpx fails (D-16)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.assets.loader import load_assets

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # cache miss

    # httpx client raises — triggers fallback to placeholder
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=Exception("network error"))

    # Use a team with a logo_url so the httpx path is exercised
    lakers_with_url = {**fixture_lakers(), "logo_url": "https://a.espncdn.com/logo.png"}

    assets = await load_assets(
        away=lakers_with_url,
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
    )

    # Both logos should be decoded RGBA images (placeholder fallback)
    assert isinstance(assets["away_logo"], Image.Image)
    assert isinstance(assets["home_logo"], Image.Image)
    assert assets["away_logo"].mode == "RGBA"
    assert assets["home_logo"].mode == "RGBA"


async def test_asset_loader_redis_hit() -> None:
    """Asset loader decodes RGBA logo from Redis cache; no httpx call made (D-16)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.assets.loader import load_assets

    png_bytes = _make_synthetic_png((64, 64))

    redis = MagicMock()
    redis.get = AsyncMock(return_value=png_bytes)  # cache hit

    http_client = MagicMock()
    http_client.get = AsyncMock()  # must NOT be called on hit

    assets = await load_assets(
        away=fixture_lakers(),
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
    )

    assert assets["away_logo"].mode == "RGBA"
    assert assets["home_logo"].mode == "RGBA"
    # No httpx call on a Redis hit
    http_client.get.assert_not_called()


async def test_asset_loader_refetch_on_miss() -> None:
    """Loader re-fetches logo on Redis miss and re-caches it (D-16, CACHE-01)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.assets.loader import load_assets
    from matchup_thumbs.settings import settings

    png_bytes = _make_synthetic_png((64, 64))

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.set = AsyncMock(return_value=None)

    # httpx returns valid PNG bytes
    mock_response = MagicMock()
    mock_response.content = png_bytes
    mock_response.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=mock_response)

    lakers_with_url = {**fixture_lakers(), "logo_url": "https://a.espncdn.com/logo.png"}

    assets = await load_assets(
        away=lakers_with_url,
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
    )

    assert assets["away_logo"].mode == "RGBA"
    # Verify re-cache was called with logo_cache_ttl for the away team's key
    expected_key = f"logo:nba:{lakers_with_url['espn_id']}".encode()
    redis.set.assert_any_call(expected_key, png_bytes, ex=settings.logo_cache_ttl)
