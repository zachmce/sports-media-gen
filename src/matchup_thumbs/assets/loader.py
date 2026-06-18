"""Asset loader — the only I/O component in the render pipeline (D-16).

Reads each team's logo bytes from Redis using a variant-aware key
``logo:{league}:{provider_id}:{variant}`` (D-08), re-fetches on a cache miss
through the fallback chain below, and decodes bytes to ``PIL.Image`` (RGBA
mode) so that generator functions remain pure with no I/O (GEN-04).

Variant fallback chain (D-06)
------------------------------
On a Redis miss, the fetch URL is resolved in order:

1. ``team["logo_variants"][variant]``  — the explicitly requested variant.
2. ``team["logo_variants"]["dark"]``   — dark variant if the requested one is absent.
3. ``team["logo_variants"]["default"]``— light/default variant.
4. ``team["logo_url"]``                — legacy fallback when ``logo_variants`` is
   empty or ``None`` (teams seeded before Phase 8).
5. Bundled placeholder PNG            — terminal fallback; never raises (T-03-09).

Key contracts
-------------
- The Redis key ``logo:{league}:{provider_id}:{variant}`` matches what seed.py
  writes for the ``default`` variant
  (``f"logo:{league_slug}:{team.provider_id}:default".encode()``).
  Non-default variants are populated lazily on first request (D-10).
- The passed ``http_client`` is the application-level shared client from
  ``app.state.http_client``.  This module never instantiates its own client.
- ``redis`` is ``decode_responses=False`` (bytes in / bytes out).
- Malformed cached bytes (corrupted entry, truncated download) degrade to the
  placeholder rather than crashing the pipeline (T-03-09).
- In Phase 8 all callers still pass ``variant="default"``; the parameter is
  the seam Phase 10 will drive (D-05).
"""

from __future__ import annotations

import io
from typing import cast

import anyio
import httpx
import structlog
from PIL import Image
from redis.asyncio import Redis

from ..generators.types import LogoAssets, TeamDict
from ..metrics import espn_fetch_failures_total
from ..settings import Settings
from . import get_placeholder_logo

logger = structlog.get_logger()

# Safety cap for Pillow decompression-bomb defence (T-03-09).
# Logos from the ESPN CDN are never legitimately larger than a few hundred
# pixels, so 4096×4096 is a generous but sane upper limit.
_MAX_LOGO_PIXELS: int = 4096 * 4096

# Redis key namespace for league logos (LGL-04, D-04).
# Distinct from the team logo namespace ``logo:`` — no underscore, one word.
# Full key: ``leaguelogo:{slug}:{variant}``
_LEAGUE_LOGO_KEY_PREFIX: str = "leaguelogo"


def _decode_logo_image(raw: bytes, max_pixels: int) -> Image.Image:
    """Decode logo bytes to an RGBA ``PIL.Image`` with a decompression-bomb cap.

    CR-01 (thread safety): the pixel cap is enforced with an explicit
    ``width * height`` check rather than by mutating the process-global
    ``Image.MAX_IMAGE_PIXELS``.  ``Image.open`` only parses the header (it does
    not decode pixels), so ``.size`` is available before the expensive
    ``.convert`` — letting us reject an oversized blob without ever touching
    shared global state.  This makes the function safe to run concurrently from
    multiple worker threads.

    CR-02 (no event-loop blocking): this helper is synchronous and CPU-bound; it
    is dispatched via ``anyio.to_thread.run_sync`` so the decode never runs on
    the event loop.

    Raises:
        PIL.Image.DecompressionBombError: if the declared pixel count exceeds
            ``max_pixels``.
        Exception: any Pillow error from malformed bytes (the async caller
            degrades these to the placeholder).
    """
    img = Image.open(io.BytesIO(raw))
    if img.width * img.height > max_pixels:
        raise Image.DecompressionBombError(
            f"logo pixel count {img.width * img.height} exceeds limit {max_pixels}"
        )
    return img.convert("RGBA")


async def _load_one_logo(
    team: TeamDict,
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
    http_client: httpx.AsyncClient,
    league: str,
    settings: Settings,
    variant: str = "default",  # Phase 10 seam (D-05); Phase 8 callers pass "default"
) -> Image.Image:
    """Fetch a single team logo as an RGBA PIL.Image.

    Variant-aware fallback chain (D-06):
    1. Redis hit on ``logo:{league}:{provider_id}:{variant}`` → decode bytes.
    2. Redis miss → resolve fetch URL via:
       a. ``team["logo_variants"][variant]``   — requested variant href.
       b. ``team["logo_variants"]["dark"]``    — dark fallback.
       c. ``team["logo_variants"]["default"]`` — default fallback.
       d. ``team["logo_url"]``                 — legacy terminal source.
    3. Fetch URL → re-cache under ``logo:{league}:{provider_id}:{variant}`` → decode.
    4. Any failure → ``get_placeholder_logo()`` → decode bytes (T-03-09).

    Malformed bytes (corrupted cache entry or bad network response) fall back
    to the placeholder rather than raising (T-03-09).

    The caller should never receive ``None`` — the placeholder is always
    available via ``importlib.resources``.
    """
    # Variant-aware key (D-08): logo:{league}:{provider_id}:{variant}
    # Matches seed.py write: f"logo:{league_slug}:{team.provider_id}:default".encode()
    key: bytes = f"logo:{league}:{team['provider_id']}:{variant}".encode()
    # decode_responses=False guarantees bytes at runtime; cast for mypy.
    raw: bytes | None = cast(bytes | None, await redis.get(key))

    if raw is None:
        # Resolve fetch URL via variant fallback chain (D-06).
        variants: dict[str, str] = team.get("logo_variants") or {}
        fetch_url: str | None = None
        for candidate in (variant, "dark", "default"):
            if fetch_url := variants.get(candidate):
                break
        if fetch_url is None:
            fetch_url = team.get("logo_url")  # legacy terminal fallback (D-06 step 4)

        if fetch_url is not None:
            try:
                resp = await http_client.get(fetch_url)
                resp.raise_for_status()
                raw = resp.content
                # Rasterize SVG→PNG off the event loop before caching (D-19 seam B —
                # lazy-fetch path).  PNG bytes pass through unchanged (D-22 ESPN no-op).
                # Lazy import: svg.py top-level import raises OSError when libcairo2 is
                # absent; deferring to call-time keeps loader.py importable everywhere.
                from ..svg import rasterize_svg_if_needed

                raw = await anyio.to_thread.run_sync(rasterize_svg_if_needed, raw)
                # Re-cache under the variant-suffixed key with the same TTL (CACHE-01).
                # The cached value is always PNG bytes (never SVG bytes).
                await redis.set(key, raw, ex=settings.logo_cache_ttl)
            except Exception as exc:
                await logger.aerror(
                    "logo_refetch_failed",
                    url=fetch_url,
                    provider_id=team["provider_id"],
                    league=league,
                    variant=variant,
                    error=str(exc),
                )
                espn_fetch_failures_total.inc()
                raw = None

    if raw is None:
        raw = get_placeholder_logo()

    # Decode bytes → PIL.Image off the event loop (CR-02) with thread-safe
    # decompression-bomb protection (CR-01, T-03-09).  Wrap in try/except so a
    # corrupted cached entry or oversized blob degrades to the placeholder
    # rather than crashing the pipeline.
    try:
        img = await anyio.to_thread.run_sync(_decode_logo_image, raw, _MAX_LOGO_PIXELS)
    except Exception as decode_exc:
        await logger.aerror(
            "logo_decode_failed",
            provider_id=team["provider_id"],
            league=league,
            error=str(decode_exc),
        )
        # Terminal fallback: decode the placeholder (it is always valid PNG).
        placeholder_raw = get_placeholder_logo()
        img = await anyio.to_thread.run_sync(
            _decode_logo_image, placeholder_raw, _MAX_LOGO_PIXELS
        )

    return img


async def load_league_logo(
    slug: str,
    variant: str,
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
    settings: Settings,
) -> Image.Image | None:
    """Fetch and decode a league logo from Redis with a variant→default fallback.

    Reads ``leaguelogo:{slug}:{variant}`` first; if that key is a cache miss,
    falls back to ``leaguelogo:{slug}:default`` before returning ``None``.  This
    mirrors the team-logo ``_load_one_logo`` variant→dark→default fallback
    discipline (D-07 graceful degradation, AGENTS.md).

    The fallback is Redis-read only — no network I/O is performed (D-07 /
    11-CONTEXT D-10).  ``redis.set`` is never called here.

    Candidate order (de-duplicated so a ``variant=="default"`` request yields a
    single ``["default"]`` entry — no looping second read):

    1. ``leaguelogo:{slug}:{variant}``   — the explicitly requested variant.
    2. ``leaguelogo:{slug}:default``     — fallback; skipped when variant is
                                           already ``"default"``.

    The loader returns ``None`` only when ALL candidates are cache misses or
    decode failures.  The render layer treats ``None`` as a signal to use the VS
    wordmark fallback.

    Decompression-bomb protection: every hit is decoded through the shared
    ``_decode_logo_image`` helper with the ``_MAX_LOGO_PIXELS`` cap
    (T-12-04-01 / T-03-09, CR-01).  A corrupted or oversized blob logs
    ``league_logo_decode_failed`` and is treated as a miss for that candidate
    (degrade-don't-crash — T-12-04-02).

    Does NOT perform a network fetch on miss (11-CONTEXT D-07 / D-10).

    Key: ``leaguelogo:{slug}:{variant}``

    The ``settings`` parameter is included for interface consistency (e.g.
    future TTL needs); the load path itself does not call ``redis.set``.

    Args:
        slug:     League slug (e.g. ``"nba"``), used in the Redis key.
        variant:  Logo variant key (e.g. ``"default"`` or ``"dark"``).
        redis:    Async Redis client (``decode_responses=False``).
        settings: Application settings (for interface consistency).

    Returns:
        Decoded RGBA ``PIL.Image`` when ANY candidate key is warm and valid;
        ``None`` when no candidate is warmed (degrade-don't-crash — LGL-04,
        D-07, AGENTS.md).
    """
    # Build a de-duplicated ordered candidate list: requested variant first,
    # then the "default" fallback — but only if the requested variant is not
    # already "default" (avoids a second identical read / looping).
    candidates: list[str] = [variant]
    if variant != "default":
        candidates.append("default")

    for candidate in candidates:
        key: bytes = f"{_LEAGUE_LOGO_KEY_PREFIX}:{slug}:{candidate}".encode()
        raw: bytes | None = cast(bytes | None, await redis.get(key))
        if raw is None:
            continue  # cache miss — try next candidate
        try:
            return await anyio.to_thread.run_sync(
                _decode_logo_image, raw, _MAX_LOGO_PIXELS
            )
        except Exception as exc:
            await logger.aerror(
                "league_logo_decode_failed",
                slug=slug,
                variant=candidate,
                error=str(exc),
            )
            # Corrupted/oversized blob — treat as terminal None for this
            # candidate; do NOT try further candidates (a corrupted :dark
            # should not silently re-try :default from a corrupted-:default
            # scenario; keeps degrade-don't-crash semantics without looping).
            return None

    # All candidates exhausted with no successful decode — signal VS fallback.
    return None


async def load_assets(
    away: TeamDict,
    home: TeamDict,
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
    http_client: httpx.AsyncClient,
    league: str,
    settings: Settings,
    variant: str = "default",  # Phase 10 seam (D-05); Phase 8 callers pass "default"
) -> LogoAssets:
    """Load and decode logos for both matchup teams.

    Returns a ``LogoAssets`` dict with ``away_logo`` and ``home_logo`` as RGBA
    ``PIL.Image`` objects.  The render layer (``render.py``) extends this into
    a full ``DecodedAssets`` after computing per-team contrast decisions
    (Phase 10 D-01, D-02).  All I/O is confined here so generators stay pure.

    Logo bytes are resolved from Redis using the variant-aware key
    ``logo:{league}:{provider_id}:{variant}`` and, on a miss, fetched through the
    fallback chain: requested variant → dark → default → legacy ``logo_url`` →
    bundled placeholder (D-06).  Distinct variants produce distinct cache entries
    that do not evict each other (D-08).

    Args:
        away: Resolved away-team record (from ``resolver.resolve_team``).
        home: Resolved home-team record.
        redis: Async Redis client (``decode_responses=False``).
        http_client: Shared async HTTP client (from ``app.state.http_client``).
        league: League slug used in the Redis key and error logs.
        settings: Application settings (WR-06: passed explicitly for testability).
        variant: Logo variant key to request (D-05).  Defaults to ``"default"``.
            Phase 10 drives this with the contrast-engine-selected variant.

    Returns:
        A ``LogoAssets`` dict with ``away_logo`` and ``home_logo`` as RGBA
        ``PIL.Image`` instances.  Never returns ``None`` for either field.
    """
    away_logo = await _load_one_logo(
        away, redis, http_client, league, settings, variant
    )
    home_logo = await _load_one_logo(
        home, redis, http_client, league, settings, variant
    )
    return LogoAssets(away_logo=away_logo, home_logo=home_logo)
