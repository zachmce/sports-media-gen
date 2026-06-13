"""ESPN API client helpers.

Provides:
- ``LEAGUE_ENDPOINTS``: mapping of league slug → (sport/league path, team limit).
- ``fetch_teams``: GET the ESPN teams endpoint for a league and return a parsed
  ``ESPNTeamsResponse``.
- ``select_logo_url``: choose the best logo href from an ESPN logos array.
- ``fetch_logo_bytes``: fetch logo bytes with semaphore, jitter, and tenacity retry.

All ESPN calls go through the caller-supplied ``httpx.AsyncClient`` (shared
lifespan client per AGENTS.md / D-06).  ``fetch_logo_bytes`` is decorated with
a tenacity retry on ``httpx.HTTPStatusError`` for 429/5xx back-pressure.
"""

import asyncio
import random
from typing import Final

import httpx
import structlog
import tenacity

from .models import ESPNLogo, ESPNTeamsResponse

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# League endpoint map (D-06 / RESEARCH.md Endpoint Map lines 168-190)
# Each value is (sport/league path, team limit for ?limit= param)
# NCAA uses limit=1000 (755 NCAAF, 362 NCAAB); pro leagues use limit=100.
# ---------------------------------------------------------------------------

LEAGUE_ENDPOINTS: Final[dict[str, tuple[str, int]]] = {
    "nba": ("basketball/nba", 100),
    "nfl": ("football/nfl", 100),
    "mlb": ("baseball/mlb", 100),
    "nhl": ("hockey/nhl", 100),
    "ncaaf": ("football/college-football", 1000),
    "ncaab": ("basketball/mens-college-basketball", 1000),
}


async def fetch_teams(
    client: httpx.AsyncClient,
    base_url: str,
    league_slug: str,
) -> ESPNTeamsResponse:
    """Fetch and validate team metadata from the ESPN v2 sports API.

    Args:
        client:      Shared ``httpx.AsyncClient`` from app/seed lifespan.
        base_url:    ESPN base URL from settings (e.g. ``https://site.api.espn.com``).
        league_slug: One of the six supported slugs (``nba``, ``nfl``, …).

    Returns:
        A validated ``ESPNTeamsResponse`` instance.  Schema drift raises
        ``pydantic.ValidationError`` immediately (ESPN-03 / D-07).

    Raises:
        KeyError: if ``league_slug`` is not in ``LEAGUE_ENDPOINTS``.
        httpx.HTTPStatusError: on 4xx/5xx after ``client`` transport retries.
    """
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    url = f"{base_url}/apis/site/v2/sports/{path}/teams?limit={limit}"
    await logger.adebug("espn_fetch_teams", league=league_slug, url=url)
    response = await client.get(url)
    response.raise_for_status()
    return ESPNTeamsResponse.model_validate(response.json())


def select_logo_url(logos: list[ESPNLogo]) -> str | None:
    """Choose the best logo href from an ESPN logos array (D-10 steps 1-3).

    Selection priority:
    1. Primary light logo: ``"default" in rel`` and ``"dark" not in rel``
       and ``"scoreboard" not in rel``.
    2. Dark variant (non-scoreboard): ``"dark" in rel`` and
       ``"scoreboard" not in rel``.
    3. First entry in the logos list.
    4. ``None`` if the list is empty (79 NCAAF teams → placeholder fallback).

    Never reconstructs CDN URLs — always consumes ``href`` directly (Pitfall 3).
    """
    # Step 1: primary light logo
    for logo in logos:
        if (
            "default" in logo.rel
            and "dark" not in logo.rel
            and "scoreboard" not in logo.rel
        ):
            return logo.href
    # Step 2: dark, non-scoreboard variant
    for logo in logos:
        if "dark" in logo.rel and "scoreboard" not in logo.rel:
            return logo.href
    # Step 3: first entry
    return logos[0].href if logos else None


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(httpx.HTTPStatusError),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def fetch_logo_bytes(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
    jitter_max: float,
) -> bytes:
    """Fetch logo bytes from the ESPN CDN.

    Acquires the shared semaphore (D-08 — bounds concurrent ESPN CDN requests),
    sleeps a random jitter to spread load, then GETs the image.  Decorated with
    tenacity to retry on ``httpx.HTTPStatusError`` (covers 429/5xx back-pressure).

    Args:
        client:     Shared ``httpx.AsyncClient``.
        url:        ESPN CDN URL (from ``select_logo_url``).
        sem:        ``asyncio.Semaphore`` bounding concurrent CDN fetches.
        jitter_max: Maximum random sleep in seconds before the GET (D-08).

    Returns:
        Raw image bytes.

    Raises:
        httpx.HTTPStatusError: after 3 tenacity retry attempts.
    """
    async with sem:
        await asyncio.sleep(random.uniform(0, jitter_max))
        response = await client.get(url)
        response.raise_for_status()
        return response.content
