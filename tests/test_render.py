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
# CACHE-07: render_version default must be 3 after the v1.2.1 hotfix bump (D-09)
# ---------------------------------------------------------------------------


def test_render_version_default_is_3() -> None:
    """Settings.render_version defaults to 3 after the v1.2.1 hotfix bump (CACHE-07).

    The v1.2.1 light-secondary invisible-logo fix changed rendered output, so the
    default bumps 2 → 3: all pre-existing :v2 render cache entries become
    unreachable on first post-deploy request without a Redis flush, forcing a
    re-render with the fixed contrast/variant logic.
    The nginx proxy_cache is NOT invalidated by this bump (its key is URL-based);
    nginx entries expire on their own 30-day TTL (RESEARCH Pitfall 4).
    """
    from matchup_thumbs.settings import Settings

    assert Settings().render_version == 3


# ---------------------------------------------------------------------------
# CTR-05 / D-10: _is_null_color_str truth table
# ---------------------------------------------------------------------------


def test_is_null_color_str_truth_table() -> None:
    """_is_null_color_str: True for absent/malformed strings, False for valid hex.

    Inspects the raw string — NOT the parsed tuple — so a real grey '#3A3A3A'
    is NOT treated as null even though it parses to the same tuple as NULL_PRIMARY.
    (D-10, CTR-05, RESEARCH 'Don't Hand-Roll')
    """
    from matchup_thumbs.render import _is_null_color_str

    # --- Null cases (True) ---
    assert _is_null_color_str(None) is True  # missing
    assert _is_null_color_str("") is True  # empty string
    assert _is_null_color_str("#ABC") is True  # CSS shorthand (3-digit)
    assert _is_null_color_str("ABC") is True  # missing hash, 3-char
    assert _is_null_color_str("#GGGGGG") is True  # non-hex characters
    assert _is_null_color_str("#12345") is True  # 5 hex digits (too short)
    assert _is_null_color_str("#1234567") is True  # 7 hex digits (too long)

    # --- Valid hex cases (False) ---
    assert _is_null_color_str("#3A3A3A") is False  # real grey (== NULL_PRIMARY parsed)
    assert _is_null_color_str("#9E1B32") is False  # Alabama crimson
    assert _is_null_color_str("#FFFFFF") is False  # white
    assert _is_null_color_str("#000000") is False  # black
    assert _is_null_color_str("3A3A3A") is False  # valid hex without leading #


# ---------------------------------------------------------------------------
# CTR-05 / D-10: _decide_for_team short-circuits on both-null colors
# ---------------------------------------------------------------------------


async def test_decide_for_team_null_colors_legacy_decision() -> None:
    """_decide_for_team returns a legacy grey ContrastDecision when both
    primary_color and secondary_color are absent/malformed, WITHOUT calling
    decide_contrast (D-10, CTR-05).

    The legacy decision must have:
    - background_rgb == NULL_PRIMARY (grey)
    - treatment == Treatment.NONE
    - recommended_variant is None
    """
    from unittest.mock import MagicMock, patch

    from matchup_thumbs.contrast import SelectionReason, Treatment
    from matchup_thumbs.generators._color import NULL_PRIMARY
    from matchup_thumbs.render import _decide_for_team
    from matchup_thumbs.settings import Settings

    team_no_colors: dict[str, object] = {
        **fixture_lakers(),
        "primary_color": None,
        "secondary_color": None,
    }
    placeholder_logo = Image.new("RGBA", (100, 100), (128, 128, 128, 255))
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    with patch("matchup_thumbs.render.decide_contrast") as mock_decide:
        decision = await _decide_for_team(team_no_colors, placeholder_logo, settings)  # type: ignore[arg-type]

    # Engine must NOT have been called
    mock_decide.assert_not_called()

    assert decision.background_rgb == NULL_PRIMARY
    assert decision.treatment == Treatment.NONE
    assert decision.recommended_variant is None
    # WR-01: legacy path is tagged NULL_COLOR, not PRIMARY_OK (the primary was
    # absent, not tested-and-passed) — keeps PRIMARY_OK queries unconflated.
    assert decision.reason == SelectionReason.NULL_COLOR


async def test_decide_for_team_one_valid_color_calls_engine() -> None:
    """_decide_for_team calls the engine when at least one color is valid (D-10).

    If only primary_color is absent but secondary_color is valid (or vice versa),
    the engine must still run — the CTR-05 guard requires BOTH to be null to
    short-circuit.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
    from matchup_thumbs.generators._color import NULL_SECONDARY
    from matchup_thumbs.render import _decide_for_team
    from matchup_thumbs.settings import Settings

    team_one_valid: dict[str, object] = {
        **fixture_lakers(),
        "primary_color": None,  # absent
        "secondary_color": "#9E1B32",  # valid
    }
    logo = Image.new("RGBA", (100, 100), (158, 27, 50, 255))
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    fake_decision = ContrastDecision(
        background_rgb=NULL_SECONDARY,
        background_source="secondary",
        achieved_ratio=4.5,
        recommended_variant=None,
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_OK,
    )

    with (
        patch(
            "matchup_thumbs.render.decide_contrast",
            return_value=fake_decision,
        ) as mock_dc,
        patch(
            "matchup_thumbs.render.anyio.to_thread.run_sync",
            new_callable=AsyncMock,
            return_value=(158, 27, 50),
        ),
    ):
        decision = await _decide_for_team(team_one_valid, logo, settings)  # type: ignore[arg-type]

    mock_dc.assert_called_once()
    assert decision is fake_decision


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
    mock_settings.min_contrast_ratio = 3.0  # needed by _decide_for_team (Phase 10)

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
    # Verify re-cache called with logo_cache_ttl for the variant-suffixed key (D-08)
    expected_key = f"logo:nba:{lakers_with_url['espn_id']}:default".encode()
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
    mock_settings.min_contrast_ratio = 3.0  # needed by _decide_for_team (Phase 10)

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


# ---------------------------------------------------------------------------
# LOGO-03: Variant fallback chain (D-05 / D-06 / D-08)
# ---------------------------------------------------------------------------


async def test_asset_loader_variant_fallback() -> None:
    """Loader falls back dark→default when the requested variant is absent (LOGO-03).

    Given: logo_variants has "dark" and "default" but NOT "scoreboard".
    When:  load_assets is called with variant="scoreboard".
    Then:  the loader fetches the "dark" href (first available fallback),
           caches it under the variant-suffixed key "scoreboard", and decodes it.
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.assets.loader import load_assets
    from matchup_thumbs.settings import settings

    png_bytes = _make_synthetic_png((64, 64))

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.set = AsyncMock(return_value=None)

    mock_response = MagicMock()
    mock_response.content = png_bytes
    mock_response.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=mock_response)

    dark_href = "https://a.espncdn.com/logo_dark.png"
    default_href = "https://a.espncdn.com/logo_default.png"
    lakers_with_variants = {
        **fixture_lakers(),
        "logo_url": "https://a.espncdn.com/logo_legacy.png",
        "logo_variants": {"dark": dark_href, "default": default_href},
    }

    assets = await load_assets(
        away=lakers_with_variants,
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
        settings=settings,
        variant="scoreboard",
    )

    assert assets["away_logo"].mode == "RGBA"
    # The loader should have fetched the "dark" fallback href (first available)
    http_client.get.assert_any_call(dark_href)
    # The result is cached under the *requested* variant key (not "dark")
    expected_key = f"logo:nba:{lakers_with_variants['espn_id']}:scoreboard".encode()
    redis.set.assert_any_call(expected_key, png_bytes, ex=settings.logo_cache_ttl)


async def test_asset_loader_fallback_to_logo_url() -> None:
    """Loader falls back to legacy logo_url when logo_variants is None (LOGO-03, D-06).

    Given: logo_variants is None (team seeded before Phase 8).
    When:  load_assets is called with variant="default".
    Then:  the loader fetches the legacy logo_url href and caches it under :default.
    """
    from unittest.mock import AsyncMock, MagicMock

    from matchup_thumbs.assets.loader import load_assets
    from matchup_thumbs.settings import settings

    png_bytes = _make_synthetic_png((64, 64))

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.set = AsyncMock(return_value=None)

    mock_response = MagicMock()
    mock_response.content = png_bytes
    mock_response.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=mock_response)

    legacy_href = "https://a.espncdn.com/logo_legacy.png"
    lakers_no_variants = {
        **fixture_lakers(),
        "logo_url": legacy_href,
        "logo_variants": None,  # no variant data (pre-Phase-8 row)
    }

    assets = await load_assets(
        away=lakers_no_variants,
        home=fixture_clippers(),
        redis=redis,
        http_client=http_client,
        league="nba",
        settings=settings,
    )

    assert assets["away_logo"].mode == "RGBA"
    # Loader must have fetched the legacy logo_url
    http_client.get.assert_any_call(legacy_href)
    # Cached under the :default variant key
    expected_key = f"logo:nba:{lakers_no_variants['espn_id']}:default".encode()
    redis.set.assert_any_call(expected_key, png_bytes, ex=settings.logo_cache_ttl)


# ---------------------------------------------------------------------------
# v1.2.1 hotfix — prong 2: post-load variant contrast re-check + fallback
#
# WHY prior gates missed the white-on-white bug: existing render fixtures use
# logo_variants=None + solid crimson logos, so PASS 2 never swaps to a variant
# and the loaded logo == the logo the decision was computed against.  The
# Lakers/Clippers golden never swaps either.  These tests exercise the path that
# DOES swap: a variant is recommended, the loaded variant is a different colour
# than the default, and that variant fails contrast against the chosen
# background.
# ---------------------------------------------------------------------------


async def test_variant_recheck_falls_back_when_variant_invisible() -> None:
    """Prong 2 unit test: a loaded variant that fails contrast → default logo.

    Reproduces the Alabama case at the render layer: the decision swapped the
    background to a light secondary (white) and recommended a variant, but the
    loaded variant is a WHITE logo → white-on-white (ratio 1.0).  The re-check
    must return the crimson DEFAULT logo (which contrasts white) and its ratio.
    """
    from unittest.mock import MagicMock

    from matchup_thumbs.contrast import (
        ContrastDecision,
        SelectionReason,
        Treatment,
        contrast_ratio,
    )
    from matchup_thumbs.render import _resolve_variant_logo
    from matchup_thumbs.settings import Settings

    white_variant = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    crimson_default = Image.new("RGBA", (100, 100), (158, 27, 50, 255))

    decision = ContrastDecision(
        background_rgb=(255, 255, 255),  # swapped to a light (white) secondary
        background_source="secondary",
        achieved_ratio=7.84,  # crimson default vs white — passed at decision time
        recommended_variant="dark",  # ESPN "dark" = a WHITE logo
        treatment=Treatment.NONE,
        reason=SelectionReason.SWAPPED_TO_SECONDARY,
    )
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    logo, ratio = await _resolve_variant_logo(
        white_variant,
        crimson_default,
        decision,
        settings,
        league="ncaa/football",
        espn_id="333",
    )

    # White variant on white bg is invisible → fell back to the crimson default,
    # and the returned ratio is the default's (crimson vs white, clears the bar).
    assert logo is crimson_default
    assert ratio == pytest.approx(contrast_ratio((158, 27, 50), (255, 255, 255)))
    assert ratio >= settings.min_contrast_ratio


async def test_variant_recheck_keeps_variant_when_contrast_holds() -> None:
    """Prong 2 unit test: a loaded variant that contrasts is kept as-is."""
    from unittest.mock import MagicMock

    from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
    from matchup_thumbs.render import _resolve_variant_logo
    from matchup_thumbs.settings import Settings

    # Dark navy background; a white variant contrasts it well (~>7:1).
    white_variant = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    crimson_default = Image.new("RGBA", (100, 100), (158, 27, 50, 255))

    decision = ContrastDecision(
        background_rgb=(29, 66, 138),  # dark navy — white logo is fine here
        background_source="primary",
        achieved_ratio=8.0,
        recommended_variant="dark",
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_LIGHT_VARIANT,
    )
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    logo, ratio = await _resolve_variant_logo(
        white_variant,
        crimson_default,
        decision,
        settings,
        league="ncaa/football",
        espn_id="333",
    )

    assert logo is white_variant
    assert ratio >= settings.min_contrast_ratio


async def test_variant_recheck_noop_when_no_variant_requested() -> None:
    """Prong 2 unit test: recommended_variant=None returns the loaded logo as-is."""
    from unittest.mock import MagicMock

    from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
    from matchup_thumbs.render import _resolve_variant_logo
    from matchup_thumbs.settings import Settings

    loaded = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    default = Image.new("RGBA", (100, 100), (158, 27, 50, 255))

    decision = ContrastDecision(
        background_rgb=(255, 255, 255),
        background_source="primary",
        achieved_ratio=5.0,
        recommended_variant=None,  # no swap happened
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_OK,
    )
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    logo, ratio = await _resolve_variant_logo(
        loaded, default, decision, settings, league="nba", espn_id="13"
    )

    # No variant was requested → loaded logo returned unchanged with the decision's
    # own achieved_ratio (no contrast computation that could mis-fire).
    assert logo is loaded
    assert ratio == 5.0


async def test_enforce_logo_contrast_escalates_to_outline() -> None:
    """When the best logo still under-contrasts the bg, escalate to OUTLINE.

    Reproduces the Cincinnati Reds / Detroit Red Wings case: the vibrant strategy
    kept the primary (red) background and requested the "dark" variant, but that
    variant is itself a RED logo (not solid white).  Both the variant and the
    default clash with the red background, so no image swap can fix it — the
    render layer must force an OUTLINE halo so the generator draws a contrasting
    silhouette.  Crisp white-on-colour logos (which already clear the bar) must
    NOT be escalated.
    """
    from unittest.mock import MagicMock

    from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
    from matchup_thumbs.render import _enforce_logo_contrast
    from matchup_thumbs.settings import Settings

    red_bg = (198, 1, 31)  # Reds red primary
    red_variant = Image.new("RGBA", (100, 100), (198, 1, 31, 255))  # "dark" = red
    red_default = Image.new("RGBA", (100, 100), (198, 1, 31, 255))  # default = red

    decision = ContrastDecision(
        background_rgb=red_bg,
        background_source="primary",
        achieved_ratio=4.0,  # optimistic (engine modelled the variant as white)
        recommended_variant="dark",
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_LIGHT_VARIANT,
    )
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    logo, new_decision = await _enforce_logo_contrast(
        red_variant,
        red_default,
        decision,
        settings,
        league="mlb",
        espn_id="17",
    )

    # Red-on-red can't be fixed by swapping the image → OUTLINE is forced and the
    # recorded ratio is corrected to the measured (sub-threshold) value.
    assert new_decision.treatment == Treatment.OUTLINE
    assert new_decision.achieved_ratio < settings.min_contrast_ratio
    assert isinstance(logo, Image.Image)


async def test_enforce_logo_contrast_no_outline_when_legible() -> None:
    """A crisp white-on-colour logo clears the bar → decision untouched, no halo."""
    from unittest.mock import MagicMock

    from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
    from matchup_thumbs.render import _enforce_logo_contrast
    from matchup_thumbs.settings import Settings

    crimson_bg = (158, 27, 50)
    white_variant = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    crimson_default = Image.new("RGBA", (100, 100), (158, 27, 50, 255))

    decision = ContrastDecision(
        background_rgb=crimson_bg,
        background_source="primary",
        achieved_ratio=5.9,
        recommended_variant="dark",
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_LIGHT_VARIANT,
    )
    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    logo, new_decision = await _enforce_logo_contrast(
        white_variant,
        crimson_default,
        decision,
        settings,
        league="ncaa/football",
        espn_id="333",
    )

    assert logo is white_variant
    assert new_decision.treatment == Treatment.NONE  # no halo on a legible logo
    assert new_decision is decision  # unchanged when already legible


async def test_render_variant_swap_avoids_invisible_logo() -> None:
    """End-to-end regression: the variant-swap path never reaches the generator
    with an invisible (white-on-white) home logo.

    This is the path prior gates missed: home team has logo_variants present, the
    decision swaps to a light secondary and recommends the "dark" (white) variant,
    and PASS 2 loads that white variant.  Prong 2 must replace it with the crimson
    default logo BEFORE it is handed to the generator.

    We capture the DecodedAssets passed to the generator and assert the home logo
    actually contrasts its chosen background at/above min_contrast_ratio.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from matchup_thumbs.contrast import (
        ContrastDecision,
        SelectionReason,
        Treatment,
        contrast_ratio,
        dominant_color,
    )
    from matchup_thumbs.generators.types import DecodedAssets
    from matchup_thumbs.render import _render_and_encode
    from matchup_thumbs.settings import Settings

    white_logo = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    crimson_logo = Image.new("RGBA", (100, 100), (158, 27, 50, 255))
    purple_logo = Image.new("RGBA", (100, 100), (85, 37, 131, 255))

    # Home team: white secondary + a "dark" variant key present (the swap path).
    home_with_variants: dict[str, object] = {
        **fixture_clippers(),
        "primary_color": "#9E1B32",  # crimson — fails vs crimson logo
        "secondary_color": "#ffffff",  # light → engine swaps here
        "logo_variants": {
            "default": "https://example.com/default.png",
            "dark": "https://example.com/dark.png",
        },
    }
    away = fixture_lakers()  # no variants — control

    # Decisions: away keeps its default; home swapped to white secondary and
    # (pre-prong-1, hypothetically) recommends the white "dark" variant.  We feed
    # this directly to prove prong 2 catches it even if a "dark" recommendation
    # ever reaches the render layer.
    away_decision = ContrastDecision(
        background_rgb=(85, 37, 131),
        background_source="primary",
        achieved_ratio=6.0,
        recommended_variant=None,
        treatment=Treatment.NONE,
        reason=SelectionReason.PRIMARY_OK,
    )
    home_decision = ContrastDecision(
        background_rgb=(255, 255, 255),  # white secondary
        background_source="secondary",
        achieved_ratio=7.84,  # crimson default vs white at decision time
        recommended_variant="dark",  # white logo → would be invisible
        treatment=Treatment.NONE,
        reason=SelectionReason.SWAPPED_TO_SECONDARY,
    )

    async def fake_load_assets(*args: object, **kwargs: object) -> dict[str, object]:
        # PASS 1: away=purple default, home=crimson default.
        return {"away_logo": purple_logo, "home_logo": crimson_logo}

    async def fake_load_one_logo(
        team: object,
        redis: object,
        http_client: object,
        league: object,
        settings: object,
        variant: str = "default",
    ) -> Image.Image:
        # PASS 2: the "dark" variant resolves to a WHITE logo; default → crimson.
        if variant == "dark":
            return white_logo
        if team is away:
            return purple_logo
        return crimson_logo

    captured: dict[str, DecodedAssets] = {}

    def spy_generator(away: object, home: object, assets: DecodedAssets) -> Image.Image:
        captured["assets"] = assets
        return Image.new("RGB", (1280, 720), (0, 0, 0))

    settings = MagicMock(spec=Settings)
    settings.min_contrast_ratio = 3.0

    redis = MagicMock()
    http_client = MagicMock()

    with (
        patch("matchup_thumbs.render.load_assets", side_effect=fake_load_assets),
        patch(
            "matchup_thumbs.render._decide_for_team",
            new_callable=AsyncMock,
            side_effect=[away_decision, home_decision],
        ),
        patch("matchup_thumbs.render._load_one_logo", side_effect=fake_load_one_logo),
        patch("matchup_thumbs.render.get_generator", return_value=spy_generator),
        patch(
            "matchup_thumbs.render.load_league_logo",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        out = await _render_and_encode(
            league="ncaa/football",
            away=away,  # type: ignore[arg-type]
            home=home_with_variants,  # type: ignore[arg-type]
            kind="thumb",
            style=0,
            redis=redis,
            http_client=http_client,
            settings=settings,
        )

    assert isinstance(out, bytes) and len(out) > 0

    # The generator must have received a home logo that CONTRASTS its background —
    # i.e. the crimson default, not the invisible white "dark" variant.
    home_logo_used = captured["assets"]["home_logo"]
    used_repr = dominant_color(home_logo_used)
    ratio = contrast_ratio(used_repr, home_decision.background_rgb)
    assert ratio >= settings.min_contrast_ratio, (
        f"home logo is invisible against its background (ratio {ratio:.2f}) — "
        "prong 2 did not fall back to the default logo"
    )
    # Concretely: it is the crimson default, not the white variant.
    assert home_logo_used is crimson_logo
