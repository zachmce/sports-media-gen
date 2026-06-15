"""Render pipeline tests (CACHE-01..05, OUT-01..03, GEN-04, GEN-07, D-14, D-16).

Unit tests use mock_redis (from conftest.py) and synthetic PNG fixtures.
No live services required for the unit suite.

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


def _make_synthetic_png(size: tuple[int, int] = (100, 100)) -> bytes:
    """Return PNG bytes for a solid-grey image of the given size."""
    buf = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GEN-07: Unknown kind/style returns 400 (raises UnknownGeneratorError)
# ---------------------------------------------------------------------------


async def test_unknown_kind_raises() -> None:
    """render_pipeline raises UnknownGeneratorError for unknown kind (GEN-07).

    The error must be raised BEFORE redis.get is called — no Redis work
    should occur for an invalid (kind, style) combination (T-03-01).
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import UnknownGeneratorError, render_pipeline
    from matchup_thumbs.settings import settings

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)
    redis.delete = AsyncMock()

    http_client = MagicMock()

    with pytest.raises(UnknownGeneratorError) as exc_info:
        await render_pipeline(
            league="nba",
            away=fixture_lakers(),
            home=fixture_clippers(),
            kind="unknown_kind",
            style=0,
            redis=redis,
            http_client=http_client,
            settings=settings,
        )

    assert exc_info.value.kind == "unknown_kind"
    assert exc_info.value.style == 0
    # No Redis operations before the raise (GEN-07 / T-03-01)
    redis.get.assert_not_called()


# ---------------------------------------------------------------------------
# CACHE-05: Cache-Control constant has the required immutable directive
# ---------------------------------------------------------------------------


def test_cache_control_constant() -> None:
    """CACHE_CONTROL_IMMUTABLE equals the required header value (CACHE-05)."""
    from matchup_thumbs.render import CACHE_CONTROL_IMMUTABLE

    assert CACHE_CONTROL_IMMUTABLE == "public, max-age=2592000, immutable"


# ---------------------------------------------------------------------------
# GEN-04: Generator has no I/O; runs in threadpool
# ---------------------------------------------------------------------------


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


def test_webp_output_logo_lossless() -> None:
    """post_cache_transform uses lossless WebP for kind='logo' (D-10)."""
    from matchup_thumbs.render import post_cache_transform

    # Lossless WebP for logo — should not raise and must be valid WebP
    png = _make_synthetic_png((100, 100))
    webp_bytes, content_type = post_cache_transform(
        png, kind="logo", fmt="webp", requested_w=None
    )

    assert content_type == "image/webp"
    img = Image.open(io.BytesIO(webp_bytes))
    assert img.format == "WEBP"


# ---------------------------------------------------------------------------
# OUT-02: Width clamp produces correct dimensions
# ---------------------------------------------------------------------------


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


async def test_render_writes_cache() -> None:
    """render_pipeline writes PNG bytes to Redis with render_cache_ttl (CACHE-01).

    The lock-acquire set call (nx=True) returns True to simulate the holder path.
    The subsequent cache-write set call uses ex=render_cache_ttl.
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline
    from matchup_thumbs.settings import settings

    # Simulate: cache miss → lock acquired → render → cache write
    # set() returns True on first call (lock acquired), None afterwards.
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # always cache miss
    redis.set = AsyncMock(side_effect=[True, None])  # lock acquired, then cache write
    redis.delete = AsyncMock()
    # CR-01: lock release now uses compare-and-delete via Lua EVAL, not delete()
    redis.eval = AsyncMock(return_value=1)

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

    assert isinstance(result.png, bytes)
    assert len(result.png) > 0
    assert result.tier == "miss"

    # Verify a cache write call with ex=render_cache_ttl occurred (CACHE-01)
    ttl = settings.render_cache_ttl
    render_write_calls = [
        c for c in redis.set.call_args_list if c.kwargs.get("ex") == ttl
    ]
    assert len(render_write_calls) >= 1

    # CR-01 / IN-05: lock release uses compare-and-delete (Lua EVAL), not
    # unconditional delete().  Verify eval was called and delete was NOT called.
    redis.eval.assert_called_once()
    eval_call = redis.eval.call_args
    # EVAL args: (script, num_keys, lock_key, lock_id) — 4 positional args
    assert eval_call.args[1] == 1, "numkeys must be 1"
    redis.delete.assert_not_called()


# ---------------------------------------------------------------------------
# CACHE-02/03: Cache key includes render_version; bump → new key
# ---------------------------------------------------------------------------


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


async def test_cache_hit_no_rerender() -> None:
    """Cache hit returns cached bytes; generator is not called (CACHE-04)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline
    from matchup_thumbs.settings import settings

    png_bytes = _make_synthetic_png()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=png_bytes)  # cache hit on first call
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

    assert result.png == png_bytes
    assert result.tier == "hit"
    redis.set.assert_not_called()  # no write on cache hit


# ---------------------------------------------------------------------------
# CACHE-04: Singleflight — waiter gets result from holder
# ---------------------------------------------------------------------------


async def test_singleflight_waiter() -> None:
    """Singleflight waiter polls and returns the holder's cached result (CACHE-04)."""
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline
    from matchup_thumbs.settings import Settings

    png_bytes = _make_synthetic_png()

    # get() call order:
    #   [0] render key cache check → miss (None)
    #   [1] first poll → still None
    #   [2] second poll → holder has written (png_bytes)
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=[None, None, png_bytes])
    # set() returns None → lock NOT acquired (another holder has it)
    redis.set = AsyncMock(return_value=None)
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

    assert result.png == png_bytes
    assert result.tier == "coalesced"


# ---------------------------------------------------------------------------
# CACHE-04 / D-14: Singleflight degrade — waiter renders locally on timeout
# ---------------------------------------------------------------------------


async def test_singleflight_degrade() -> None:
    """Degraded fallback: waiter renders locally when max_wait elapses (D-14).

    Redis never provides a result, so the waiter degrades to a local render.
    The degraded path calls _render_and_encode which uses load_assets (Redis
    logo miss → placeholder) and the real generator via anyio threadpool.
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline
    from matchup_thumbs.settings import Settings

    # Redis always returns None — render key cache miss, logo cache miss
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    # set() returns None → lock NOT acquired; triggers waiter path
    redis.set = AsyncMock(return_value=None)
    redis.delete = AsyncMock()

    mock_settings = MagicMock(spec=Settings)
    mock_settings.render_version = 1
    mock_settings.sf_lock_ttl = 10
    mock_settings.sf_poll_interval = 0.001  # tiny so the loop exits fast
    mock_settings.sf_max_wait = 0.005  # very short → degrade immediately
    mock_settings.render_cache_ttl = 60
    mock_settings.logo_cache_ttl = 60  # used by load_assets on re-fetch miss

    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=Exception("network unreachable"))

    # Degraded render should complete (using placeholder logos) without raising
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

    assert isinstance(result.png, bytes)
    assert len(result.png) > 0
    assert result.tier == "degraded"


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

    from matchup_thumbs.settings import settings

    assets = await load_assets(
        away=lakers_with_url,
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
        settings=settings,
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

    from matchup_thumbs.settings import settings

    assets = await load_assets(
        away=fixture_lakers(),
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
        settings=settings,
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
        settings=settings,
    )

    assert assets["away_logo"].mode == "RGBA"
    # Verify re-cache was called with logo_cache_ttl for the away team's key
    expected_key = f"logo:nba:{lakers_with_url['espn_id']}".encode()
    redis.set.assert_any_call(expected_key, png_bytes, ex=settings.logo_cache_ttl)


# ---------------------------------------------------------------------------
# WR-03: Unknown fmt raises ValueError (not silently falls through to PNG)
# ---------------------------------------------------------------------------


def test_unknown_fmt_raises() -> None:
    """post_cache_transform raises BadTransformParam for unsupported fmt (WR-03)."""
    from matchup_thumbs.render import BadTransformParam, post_cache_transform

    png = _make_synthetic_png((100, 100))
    with pytest.raises(BadTransformParam) as exc_info:
        post_cache_transform(png, kind="thumb", fmt="jpeg", requested_w=None)
    assert exc_info.value.param == "fmt"

    with pytest.raises(BadTransformParam) as exc_info2:
        post_cache_transform(png, kind="thumb", fmt="", requested_w=None)
    assert exc_info2.value.param == "fmt"

    with pytest.raises(BadTransformParam) as exc_info3:
        post_cache_transform(png, kind="thumb", fmt="wepb", requested_w=None)
    assert exc_info3.value.param == "fmt"


# ---------------------------------------------------------------------------
# WR-04: Non-positive requested_w raises ValueError
# ---------------------------------------------------------------------------


def test_nonpositive_width_raises() -> None:
    """post_cache_transform raises BadTransformParam for requested_w <= 0 (WR-04)."""
    from matchup_thumbs.render import BadTransformParam, post_cache_transform

    png = _make_synthetic_png((200, 100))

    with pytest.raises(BadTransformParam) as exc_info:
        post_cache_transform(png, kind="thumb", fmt="png", requested_w=0)
    assert exc_info.value.param == "w"

    with pytest.raises(BadTransformParam) as exc_info2:
        post_cache_transform(png, kind="thumb", fmt="png", requested_w=-50)
    assert exc_info2.value.param == "w"


# ---------------------------------------------------------------------------
# CR-02: post_cache_transform rejects malformed/truncated PNG bytes
# ---------------------------------------------------------------------------


def test_post_cache_transform_rejects_malformed_bytes() -> None:
    """post_cache_transform raises on malformed PNG bytes (CR-02, T-03-09)."""
    from matchup_thumbs.render import post_cache_transform

    # PIL raises OSError (or its subclass UnidentifiedImageError) on bad input
    with pytest.raises(OSError):
        post_cache_transform(
            b"not a png at all", kind="thumb", fmt="png", requested_w=None
        )

    # Truncated PNG header — valid magic but corrupted body
    valid_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    with pytest.raises(OSError):
        post_cache_transform(valid_magic, kind="thumb", fmt="png", requested_w=None)


# ---------------------------------------------------------------------------
# WR-01: Degraded fallback writes result to render cache
# ---------------------------------------------------------------------------


async def test_singleflight_degrade_writes_cache() -> None:
    """Degraded path writes rendered bytes to the render cache (WR-01).

    Subsequent requests should get a cache hit rather than degrading again.
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.render import render_pipeline
    from matchup_thumbs.settings import Settings

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # always miss
    # set() returns None → lock NOT acquired; waiter path
    redis.set = AsyncMock(return_value=None)
    redis.delete = AsyncMock()

    mock_settings = MagicMock(spec=Settings)
    mock_settings.render_version = 1
    mock_settings.sf_lock_ttl = 10
    mock_settings.sf_poll_interval = 0.001
    mock_settings.sf_max_wait = 0.005  # very short → degrade immediately
    mock_settings.render_cache_ttl = 60
    mock_settings.logo_cache_ttl = 60

    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=Exception("network unreachable"))

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

    assert isinstance(result.png, bytes)
    assert len(result.png) > 0
    assert result.tier == "degraded"

    # WR-01: verify at least one redis.set call used ex=render_cache_ttl
    # (the degraded cache-populate write).
    cache_writes = [c for c in redis.set.call_args_list if c.kwargs.get("ex") == 60]
    assert len(cache_writes) >= 1, (
        "Degraded path must write rendered bytes to the render cache (WR-01)"
    )


# ---------------------------------------------------------------------------
# CR-01 / CR-02: Pillow concurrency hardening (render-pillow-concurrency)
#
# The decompression-bomb cap must be enforced WITHOUT mutating the process-
# global Image.MAX_IMAGE_PIXELS (thread-unsafe under concurrent renders), and
# logo decode must run off the event loop.
# ---------------------------------------------------------------------------


def test_post_cache_transform_rejects_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post_cache_transform raises DecompressionBombError when the declared
    pixel count exceeds the cap — enforced by an explicit check, not the global
    (CR-01)."""
    import matchup_thumbs.render as render_mod

    # Lower the cap so a cheap 100×100 (10 000 px) image trips it.
    monkeypatch.setattr(render_mod, "_MAX_RENDER_PIXELS", 100)
    png = _make_synthetic_png((100, 100))

    with pytest.raises(Image.DecompressionBombError):
        render_mod.post_cache_transform(png, kind="thumb", fmt="png", requested_w=None)


def test_post_cache_transform_does_not_mutate_global() -> None:
    """post_cache_transform leaves the process-global Image.MAX_IMAGE_PIXELS
    untouched (CR-01 — no global save/restore race)."""
    from matchup_thumbs.render import post_cache_transform

    before = Image.MAX_IMAGE_PIXELS
    png = _make_synthetic_png((120, 80))
    post_cache_transform(png, kind="thumb", fmt="png", requested_w=None)
    assert before == Image.MAX_IMAGE_PIXELS


async def test_load_one_logo_oversize_falls_back_to_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_load_one_logo degrades to the placeholder when the decoded logo exceeds
    the pixel cap, and never mutates the global (CR-01).  The decode is
    dispatched off the event loop via anyio.to_thread.run_sync (CR-02)."""
    from unittest.mock import AsyncMock, MagicMock

    import matchup_thumbs.assets.loader as loader_mod
    from matchup_thumbs.assets.loader import _load_one_logo
    from matchup_thumbs.settings import settings

    # Cap sits between the 512×512 placeholder (262 144 px, must still decode)
    # and the 600×600 cached logo (360 000 px, must be rejected as oversized).
    monkeypatch.setattr(loader_mod, "_MAX_LOGO_PIXELS", 300_000)
    before = Image.MAX_IMAGE_PIXELS

    redis = MagicMock()
    redis.get = AsyncMock(return_value=_make_synthetic_png((600, 600)))
    redis.set = AsyncMock(return_value=None)
    http_client = MagicMock()

    img = await _load_one_logo(
        fixture_lakers(), redis, http_client, league="nba", settings=settings
    )

    # Placeholder fallback still yields a usable RGBA image…
    assert img.mode == "RGBA"
    # …and the global cap was never touched.
    assert before == Image.MAX_IMAGE_PIXELS
