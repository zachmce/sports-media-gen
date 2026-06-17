"""ESPN API client helpers.

Provides:
- ``LEAGUE_ENDPOINTS``: mapping of league slug → (sport/league path, team limit).
- ``fetch_teams``: GET the ESPN teams endpoint for a league and return a parsed
  ``ESPNTeamsResponse``.
- ``select_logo_url``: choose the best logo href from an ESPN logos array.
- ``derive_variant_key``: derive a canonical variant key from ESPN logo rel tags.
- ``build_logo_variants``: build a full variant map from an ESPN logos array.
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


async def fetch_league_logo_data(
    client: httpx.AsyncClient,
    core_api_base_url: str,
    league_slug: str,
) -> list[ESPNLogo]:
    """Fetch the logos array from the ESPN core API league endpoint (LGL-01).

    URL: ``{core_api_base_url}/v2/sports/{sport}/leagues/{espn_league_slug}``

    The logos array is embedded inline in the root response (no ``$ref`` follow
    needed — the league root object includes ``logos`` directly). [VERIFIED: live probe]

    SSRF gate (T-11-01): the slug is validated against KNOWN_LEAGUES in the
    seed ``run()`` entry point before this function is called.  The
    ``LEAGUE_ENDPOINTS[league_slug]`` lookup additionally constrains the URL
    path to the known set — a ``KeyError`` is raised for any unknown slug.
    The URL is never constructed from a raw/unvalidated string.

    Args:
        client:             Shared ``httpx.AsyncClient`` (caller-supplied).
        core_api_base_url:  ESPN core API base URL
                            (``settings.espn_core_api_base_url``).
                            DISTINCT from ``espn_base_url`` (site.api.espn.com).
        league_slug:        One of the six supported slugs (``nba``, ``nfl``, …).
                            Must be a key in ``LEAGUE_ENDPOINTS``.

    Returns:
        Parsed ``list[ESPNLogo]`` from the inline ``logos`` array; empty list on
        any HTTP error so a single league failure never aborts the entire seed.

    Raises:
        KeyError: if ``league_slug`` is not in ``LEAGUE_ENDPOINTS`` (SSRF gate).
    """
    # KeyError on unknown slug (SSRF gate — never build URL before this lookup)
    path, _ = LEAGUE_ENDPOINTS[league_slug]
    sport, espn_league_slug = path.split("/", 1)
    url = f"{core_api_base_url}/v2/sports/{sport}/leagues/{espn_league_slug}"
    await logger.adebug("espn_fetch_league_logo", league=league_slug, url=url)
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        await logger.aerror(
            "league_logo_fetch_failed",
            league=league_slug,
            url=url,
            status_code=exc.response.status_code,
        )
        return []
    data: dict[str, object] = response.json()
    raw_logos: list[object] = data.get("logos", [])  # type: ignore[assignment]
    return [ESPNLogo.model_validate(logo) for logo in raw_logos]


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


def derive_variant_key(rel: list[str]) -> str:
    """Derive canonical variant key from ESPN logo rel tags (D-03 / LOGO-01).

    Drops the generic size token ``"full"`` and joins the remaining tags sorted
    alphabetically with ``"_"``.  An empty remainder maps to ``"default"``.

    Examples::

        ["full", "default"]                    -> "default"
        ["full", "dark"]                       -> "dark"
        ["full", "scoreboard"]                 -> "scoreboard"
        ["full", "scoreboard", "dark"]         -> "dark_scoreboard"
        ["full", "grayscale"]                  -> "grayscale"
        ["full", "primary_logo_on_primary_color"] -> "primary_logo_on_primary_color"
        ["full"]                               -> "default"  (empty remainder)
    """
    remaining = sorted(r for r in rel if r != "full")
    return "_".join(remaining) if remaining else "default"


def build_logo_variants(
    logos: list[ESPNLogo],
    team_slug: str,
    league_slug: str,
) -> dict[str, str]:
    """Build canonical variant map from an ESPN logos array (D-03 / LOGO-01).

    Iterates every logo entry, derives its canonical key via ``derive_variant_key``,
    and stores ``key → href``.  On key collision (two logos produce the same derived
    key) the last entry wins and a warning is logged (last-write-wins per D-03).

    Args:
        logos:       ESPN logos list from ``ESPNTeamEntry.logos``.
        team_slug:   Team slug for collision-warning context.
        league_slug: League slug for collision-warning context.

    Returns:
        Mapping of canonical variant key → ESPN CDN href.  Empty if ``logos`` is
        empty (e.g. the 79 NCAAF teams with no logos).
    """
    variants: dict[str, str] = {}
    for logo in logos:
        key = derive_variant_key(logo.rel)
        if key in variants:
            logger.warning(
                "logo_variant_key_collision",
                key=key,
                team=team_slug,
                league=league_slug,
                old_href=variants[key],
                new_href=logo.href,
            )
        variants[key] = logo.href
    return variants


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
