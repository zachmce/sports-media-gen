"""Asset loader — the only I/O component in the render pipeline (D-16).

Reads each team's logo bytes from Redis (key ``logo:{league}:{espn_id}``),
re-fetches from ``logo_url`` via the shared httpx client on a cache miss,
and falls back to the bundled placeholder on total failure.  Bytes are decoded
to ``PIL.Image`` (RGBA mode) here so that generator functions remain pure with
no I/O (GEN-04).

Key contracts
-------------
- The Redis key ``logo:{league}:{espn_id}`` matches exactly what seed.py writes
  (``f"logo:{league_slug}:{team.id}".encode()``; ``espn_id == team.id``).
- The passed ``http_client`` is the application-level shared client from
  ``app.state.http_client``.  This module never instantiates its own client.
- ``redis`` is ``decode_responses=False`` (bytes in / bytes out).
- Malformed cached bytes (corrupted entry, truncated download) degrade to the
  placeholder rather than crashing the pipeline (T-03-09).
"""

from __future__ import annotations

import io
from typing import cast

import httpx
import structlog
from PIL import Image
from redis.asyncio import Redis

from ..generators.types import DecodedAssets, TeamDict
from ..metrics import espn_fetch_failures_total
from ..settings import Settings
from . import get_placeholder_logo

logger = structlog.get_logger()

# Safety cap for Pillow decompression-bomb defence (T-03-09).
# Logos from the ESPN CDN are never legitimately larger than a few hundred
# pixels, so 4096×4096 is a generous but sane upper limit.
_MAX_LOGO_PIXELS: int = 4096 * 4096


async def _load_one_logo(
    team: TeamDict,
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
    http_client: httpx.AsyncClient,
    league: str,
    settings: Settings,
) -> Image.Image:
    """Fetch a single team logo as an RGBA PIL.Image.

    Fallback chain (D-16):
    1. Redis cache hit → decode bytes.
    2. Redis miss + ``logo_url`` set → httpx re-fetch → re-cache → decode bytes.
    3. Any failure → ``get_placeholder_logo()`` → decode bytes.

    Malformed bytes (corrupted cache entry or bad network response) fall back
    to the placeholder rather than raising (T-03-09).

    The caller should never receive ``None`` — the placeholder is always
    available via ``importlib.resources``.
    """
    # Key convention matches seed.py: f"logo:{league_slug}:{team.id}".encode()
    # espn_id == team.id in the seeded row (D-16).
    key: bytes = f"logo:{league}:{team['espn_id']}".encode()
    # decode_responses=False guarantees bytes at runtime; cast for mypy.
    raw: bytes | None = cast(bytes | None, await redis.get(key))

    logo_url: str | None = team.get("logo_url")
    if raw is None and logo_url is not None:
        try:
            resp = await http_client.get(logo_url)
            resp.raise_for_status()
            fetched: bytes = resp.content
            raw = fetched
            # Re-cache with the same TTL as the seed write (CACHE-01).
            await redis.set(key, raw, ex=settings.logo_cache_ttl)
        except Exception as exc:
            await logger.aerror(
                "logo_refetch_failed",
                url=logo_url,
                espn_id=team["espn_id"],
                league=league,
                error=str(exc),
            )
            espn_fetch_failures_total.inc()
            raw = None

    if raw is None:
        raw = get_placeholder_logo()

    # Decode bytes → PIL.Image with decompression-bomb protection (T-03-09).
    # Wrap in try/except so a corrupted cached entry degrades to placeholder
    # rather than crashing the pipeline.
    original_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_LOGO_PIXELS
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as decode_exc:
        await logger.aerror(
            "logo_decode_failed",
            espn_id=team["espn_id"],
            league=league,
            error=str(decode_exc),
        )
        # Terminal fallback: decode the placeholder (it is always valid PNG).
        placeholder_raw = get_placeholder_logo()
        img = Image.open(io.BytesIO(placeholder_raw)).convert("RGBA")
    finally:
        Image.MAX_IMAGE_PIXELS = original_max

    return img


async def load_assets(
    away: TeamDict,
    home: TeamDict,
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
    http_client: httpx.AsyncClient,
    league: str,
    settings: Settings,
) -> DecodedAssets:
    """Load and decode logos for both matchup teams.

    Returned ``DecodedAssets`` contains RGBA ``PIL.Image`` objects ready for
    consumption by pure generator functions (GEN-04).  All I/O is confined here
    so generators stay pure.

    Args:
        away: Resolved away-team record (from ``resolver.resolve_team``).
        home: Resolved home-team record.
        redis: Async Redis client (``decode_responses=False``).
        http_client: Shared async HTTP client (from ``app.state.http_client``).
        league: League slug used in the Redis key and error logs.
        settings: Application settings (WR-06: passed explicitly for testability).

    Returns:
        A ``DecodedAssets`` dict with ``away_logo`` and ``home_logo`` as RGBA
        ``PIL.Image`` instances.  Never returns ``None`` for either field.
    """
    away_logo = await _load_one_logo(away, redis, http_client, league, settings)
    home_logo = await _load_one_logo(home, redis, http_client, league, settings)
    return DecodedAssets(away_logo=away_logo, home_logo=home_logo)
