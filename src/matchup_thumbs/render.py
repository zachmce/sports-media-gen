"""Async render pipeline for matchup-thumbs.

Orchestrates the full render lifecycle:
1. Render-cache read (Redis key ``rendered:{league}:{away}:{home}:{kind}:{style}:v{N}``)
2. Redis ``SET NX`` singleflight coalescing so only one Pillow composition runs per key
3. Bounded waiter poll (sf_poll_interval cadence, sf_max_wait timeout)
4. Degraded local render fallback when the lock holder is too slow / dead (D-14)
5. Asset load via ``load_assets`` (the only I/O in the pipeline)
6. Threadpool dispatch of the pure generator via ``anyio.to_thread.run_sync`` (GEN-04)
7. PNG cache write (CACHE-01); one canonical PNG per render key (D-09)
8. Post-cache ``post_cache_transform`` for ``?w`` clamp and ``?fmt`` encode
   (OUT-01/02/03)

Security
--------
- ``get_generator(kind, style) is None`` → ``UnknownGeneratorError`` raised BEFORE any
  Redis work (GEN-07, T-03-01).  Phase 4 maps this to HTTP 400.
- Render key is built from resolver-canonical slugs and validated enums; no raw user
  string is interpolated after the Phase 4 validation layer (T-03-13).
- Singleflight lock carries a ``uuid4`` owner token; ``ex=sf_lock_ttl`` ensures a
  crashed holder cannot wedge the key (T-03-03).
- ``post_cache_transform`` clamps ``?w`` down only — never upscales (D-02, T-03-02).
  Phase 4 additionally bounds ``?w ≤ MAX_W`` before calling this function.

Constants
---------
- ``CACHE_CONTROL_IMMUTABLE``: HTTP header value for rendered responses (CACHE-05).
- ``WEBP_QUALITY``, ``WEBP_METHOD``: named constants for WebP encode params (D-10).

``post_cache_transform`` is CPU-bound; Phase 4 must run it via
``anyio.to_thread.run_sync`` when called from an async route handler.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from functools import partial
from typing import cast

import anyio
import httpx
import structlog
from PIL import Image
from redis.asyncio import Redis  # bare Redis — not generic at runtime

from .assets.loader import load_assets
from .generators import get_generator
from .generators.types import TeamDict
from .settings import Settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Singleflight: compare-and-delete Lua script (CR-01, T-03-03)
#
# Only deletes the lock if the stored value matches the caller's lock_id.
# Without this guard, a slow holder whose TTL expired would delete a different
# holder's freshly-acquired lock — defeating singleflight under contention.
# ---------------------------------------------------------------------------

_RELEASE_LOCK_LUA: str = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Cache-Control header value for rendered responses (CACHE-05).
CACHE_CONTROL_IMMUTABLE: str = "public, max-age=2592000, immutable"

#: Content-type constants for the two supported output formats.
CONTENT_TYPE_PNG: str = "image/png"
CONTENT_TYPE_WEBP: str = "image/webp"

#: WebP encoding defaults (D-10).
_WEBP_QUALITY: int = 85
_WEBP_METHOD: int = 6

#: Decompression-bomb pixel cap for post_cache_transform (CR-02, T-03-09).
#: The largest native canvas is 1280×720 = 921 600 px; 4096×4096 is a generous
#: upper bound that rejects genuinely malicious oversized blobs.
_MAX_RENDER_PIXELS: int = 4096 * 4096

#: Supported output formats (WR-03, OUT-01).
_SUPPORTED_FMTS: frozenset[str] = frozenset({"png", "webp"})


# ---------------------------------------------------------------------------
# Typed error (GEN-07)
# ---------------------------------------------------------------------------


class UnknownGeneratorError(Exception):
    """Raised when (kind, style) is not in the generator registry (GEN-07).

    Phase 4 catches this and maps it to HTTP 400.

    Args:
        kind:  The image kind that was requested (e.g. ``"bogus"``).
        style: The style index that was requested (e.g. ``99``).
    """

    def __init__(self, kind: str, style: int) -> None:
        super().__init__(f"No generator registered for kind={kind!r} style={style!r}")
        self.kind = kind
        self.style = style


# ---------------------------------------------------------------------------
# Render key builder (exposed for tests — CACHE-02/03, OUT-03)
# ---------------------------------------------------------------------------


def _build_render_key(
    league: str,
    away: TeamDict,
    home: TeamDict,
    kind: str,
    style: int,
    settings: Settings,
) -> bytes:
    """Build the Redis render cache key for the given matchup and settings.

    Key format (D-12, CACHE-02):
        rendered:{league}:{away_slug}:{home_slug}:{kind}:{style}:v{render_version}

    The key is encoded to bytes because ``app.state.redis`` is always
    ``decode_responses=False`` (bytes in / bytes out).

    The ``?fmt`` and ``?w`` parameters are deliberately absent from the key
    so that post-cache transforms share the same cached PNG (OUT-03, D-09).
    """
    return (
        f"rendered:{league}:{away['slug']}:{home['slug']}"
        f":{kind}:{style}:v{settings.render_version}"
    ).encode()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _encode_png(img: Image.Image) -> bytes:
    """Encode a Pillow image to PNG bytes.

    Uses ``optimize=True`` to reduce Redis blob size at a small CPU cost
    (~39 ms vs ~30 ms for 1280×720 — acceptable for a cached render).

    Note: Pitfall 5 (RESEARCH.md) — ``optimize=True`` is 3–4× slower than
    ``optimize=False`` for large images.  Switch if profiling shows PNG encode
    dominates composition time.
    """
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _render_and_encode(
    league: str,
    away: TeamDict,
    home: TeamDict,
    kind: str,
    style: int,
    redis: Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> bytes:
    """Load assets, dispatch the pure generator via threadpool, and return PNG bytes.

    This helper is shared by the lock holder path and the degraded fallback path.
    It is the only place where ``load_assets`` and the generator are called.

    Raises:
        UnknownGeneratorError: if (kind, style) is not registered.  Callers
            should validate before calling — this is a defence in depth guard.
    """
    assets = await load_assets(away, home, redis, http_client, league)
    gen_fn = get_generator(kind, style)
    # Guard — callers validate before reaching here; this assertion surfaces
    # any coding error (e.g. degraded path calling with an unknown kind).
    if gen_fn is None:
        raise UnknownGeneratorError(kind, style)
    # Dispatch the pure Pillow composition to a thread so the event loop
    # is never blocked (GEN-04).  abandon_on_cancel=False (default) is
    # correct — Pillow work should complete even if the waiter cancels.
    img: Image.Image = await anyio.to_thread.run_sync(
        partial(gen_fn, away, home, assets)
    )
    return _encode_png(img)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def render_pipeline(
    league: str,
    away: TeamDict,
    home: TeamDict,
    kind: str,
    style: int,
    redis: Redis,
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> bytes:
    """Return canonical PNG bytes for the given matchup.

    Cache-read → singleflight → asset load → threadpool render → cache write.
    Returns raw PNG bytes; ``?w`` and ``?fmt`` transforms are applied by the
    caller via ``post_cache_transform`` (D-09, OUT-03).

    Args:
        league:      League slug (e.g. ``"nba"``).
        away:        Resolved away-team dict (from resolver.resolve_team).
        home:        Resolved home-team dict.
        kind:        Image kind: ``"thumb"``, ``"logo"``, or ``"poster"``.
        style:       Style index (``0`` = default style per kind).
        redis:       Async Redis client (``decode_responses=False``).
        http_client: Shared async HTTP client for logo re-fetch.
        settings:    Application settings (provides render_version, TTLs, etc.).

    Returns:
        PNG bytes (canonical cached artifact).

    Raises:
        UnknownGeneratorError: when (kind, style) has no registered generator.
            Phase 4 maps this to HTTP 400 (GEN-07, T-03-01).
    """
    # ------------------------------------------------------------------
    # GEN-07 / T-03-01: Validate (kind, style) BEFORE any Redis work.
    # A bad kind/style must 400, not trigger a lock acquisition or render.
    # ------------------------------------------------------------------
    if get_generator(kind, style) is None:
        raise UnknownGeneratorError(kind, style)

    render_key = _build_render_key(league, away, home, kind, style, settings)
    # Lock key mirrors the render key with a different prefix so the poll
    # loop only needs to watch render_key (no lock key read needed).
    lock_key = render_key.replace(b"rendered:", b"renderlock:", 1)

    # ------------------------------------------------------------------
    # 1. Cache read (CACHE-04 hot path).
    # decode_responses=False guarantees bytes at runtime; cast for mypy.
    # ------------------------------------------------------------------
    cached: bytes | None = cast(bytes | None, await redis.get(render_key))
    if cached is not None:
        await logger.ainfo(
            "render_cache_hit",
            league=league,
            kind=kind,
            style=style,
        )
        return cached

    # ------------------------------------------------------------------
    # 2. Singleflight — try to acquire the render lock via SET NX (D-13).
    # Unique lock_id prevents a waiter from releasing another holder's lock.
    # ex=sf_lock_ttl bounds the lock so a crashed holder can't wedge the key.
    # ------------------------------------------------------------------
    lock_id: bytes = uuid.uuid4().hex.encode()
    acquired: bool | None = cast(
        bool | None,
        await redis.set(lock_key, lock_id, ex=settings.sf_lock_ttl, nx=True),
    )

    if acquired:
        # ----------------------------------------------------------------
        # 3. Holder path: render and write to cache.
        # Use try/finally so the lock is always released even if rendering
        # raises an exception (T-03-03).
        # ----------------------------------------------------------------
        try:
            png = await _render_and_encode(
                league, away, home, kind, style, redis, http_client, settings
            )
            # CACHE-01: store canonical PNG with long TTL (D-12).
            await redis.set(render_key, png, ex=settings.render_cache_ttl)
        finally:
            # Compare-and-delete: only release the lock if we still own it
            # (CR-01, T-03-03).  Uses a Lua EVAL so the GET + conditional DEL
            # is atomic.  If the lock_ttl expired while we were rendering,
            # another holder may have acquired the lock — we must NOT delete
            # it.  The lock_id uuid written at SET NX time is the owner token.
            await redis.eval(_RELEASE_LOCK_LUA, 1, lock_key, lock_id)
        return png

    # ------------------------------------------------------------------
    # 4. Waiter path: poll the result key until the holder writes it.
    # Poll every sf_poll_interval seconds up to sf_max_wait (D-13/CACHE-04).
    # ------------------------------------------------------------------
    waited: float = 0.0
    while waited < settings.sf_max_wait:
        await asyncio.sleep(settings.sf_poll_interval)
        waited += settings.sf_poll_interval
        result: bytes | None = cast(bytes | None, await redis.get(render_key))
        if result is not None:
            await logger.ainfo(
                "render_singleflight_waiter_resolved",
                league=league,
                kind=kind,
                style=style,
                waited_seconds=waited,
            )
            return result

    # ------------------------------------------------------------------
    # 5. Degraded fallback (D-14): render locally rather than erroring.
    # Availability over strict single-flight; the extra render is the cost.
    # ------------------------------------------------------------------
    await logger.awarning(
        "render_singleflight_timeout_degraded",
        league=league,
        kind=kind,
        style=style,
        sf_max_wait=settings.sf_max_wait,
    )
    png = await _render_and_encode(
        league, away, home, kind, style, redis, http_client, settings
    )
    # WR-01: Best-effort cache populate so subsequent waiters get a cache hit
    # instead of another degrade.  If the write fails, swallow the error — we
    # already have the bytes and availability is the priority here.
    try:
        await redis.set(render_key, png, ex=settings.render_cache_ttl)
    except Exception as exc:
        await logger.awarning(
            "degraded_cache_write_failed",
            league=league,
            kind=kind,
            style=style,
            error=str(exc),
        )
    return png


def post_cache_transform(
    png_bytes: bytes,
    kind: str,
    fmt: str,
    requested_w: int | None,
) -> tuple[bytes, str]:
    """Apply per-request ``?w`` and ``?fmt`` transforms to cached PNG bytes.

    Called after the render cache read — these parameters never enter the
    render key (D-09, OUT-03).  Returns ``(encoded_bytes, content_type)``.

    Args:
        png_bytes:   Canonical PNG bytes from the render cache (or render pipeline).
        kind:        Image kind — determines WebP lossless mode for ``"logo"`` (D-10).
        fmt:         Output format: ``"png"`` (default, D-11) or ``"webp"`` (OUT-01).
                     Any other value raises ``ValueError`` (WR-03).
        requested_w: Desired output width.  ``None`` means no resize.  A value
                     larger than the native width is clamped to native width so
                     the image is never upscaled (D-02, OUT-02, T-03-02).
                     Non-positive values raise ``ValueError`` (WR-04).

    Returns:
        ``(bytes, content_type)`` where ``content_type`` is one of
        ``"image/png"`` or ``"image/webp"``.

    Raises:
        ValueError: if ``fmt`` is not in ``{"png", "webp"}`` (WR-03), or if
            ``requested_w`` is non-positive (WR-04).

    Note:
        This function is CPU-bound (Pillow resize + encode).  When calling from
        an async FastAPI route handler, use ``anyio.to_thread.run_sync`` to
        avoid blocking the event loop (GEN-04 principle extended to transforms).
    """
    # WR-03: reject unsupported formats before any Pillow work.
    if fmt not in _SUPPORTED_FMTS:
        raise ValueError(f"Unsupported fmt: {fmt!r}; must be one of {sorted(_SUPPORTED_FMTS)}")

    # WR-04: reject non-positive widths — zero/negative corrupt resize.
    if requested_w is not None and requested_w <= 0:
        raise ValueError(f"requested_w must be positive, got {requested_w!r}")

    # CR-02: apply the same decompression-bomb discipline here as in the asset
    # loader (T-03-09).  Although png_bytes normally comes from our own render
    # cache, the function is a documented public entrypoint and a future caller
    # or poisoned cache entry could supply adversarial bytes.
    original_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_RENDER_PIXELS
    try:
        src: Image.Image = Image.open(io.BytesIO(png_bytes))
        src.load()  # force decode so the pixel cap is enforced now
    except Exception:
        Image.MAX_IMAGE_PIXELS = original_max
        raise
    finally:
        Image.MAX_IMAGE_PIXELS = original_max

    img: Image.Image = src

    if requested_w is not None:
        # D-02: clamp down only — never upscale past native width (T-03-02).
        target_w = min(requested_w, img.width)
        if target_w < img.width:
            ratio = target_w / img.width
            target_h = int(img.height * ratio)
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    if fmt == "webp":
        buf = io.BytesIO()
        # D-10: logo kind uses lossless; thumb/poster use lossy quality=85.
        lossless: bool = kind == "logo"
        img.convert("RGB").save(
            buf,
            format="WEBP",
            quality=_WEBP_QUALITY,
            lossless=lossless,
            method=_WEBP_METHOD,
        )
        return buf.getvalue(), CONTENT_TYPE_WEBP

    # PNG default (D-11, OUT-01).
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), CONTENT_TYPE_PNG
