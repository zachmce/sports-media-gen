"""MLB Stats API client helpers.

Provides:
- ``fetch_mlb_teams``: GET the MLB Stats API teams endpoint for a sportId
  and return a parsed ``MLBTeamsResponse``.

All MLB Stats API calls go through the caller-supplied ``httpx.AsyncClient``
(shared lifespan client per AGENTS.md / D-02).  The provider never creates
its own client.
"""

import httpx
import structlog

from .models import MLBTeamsResponse

logger = structlog.get_logger()


async def fetch_mlb_teams(
    client: httpx.AsyncClient,
    base_url: str,
    sport_id: int,
) -> MLBTeamsResponse:
    """Fetch and validate team metadata from the MLB Stats API.

    Args:
        client:    Shared ``httpx.AsyncClient`` (caller-supplied, D-02).
        base_url:  MLB Stats API base URL (``settings.mlb_statsapi_base_url``).
        sport_id:  Integer sportId (from ``_MILB_SPORT_IDS`` dict — SSRF gate
                   already applied upstream in ``MLBStatsProvider.fetch_teams``).

    Returns:
        A validated ``MLBTeamsResponse``.  Schema drift raises
        ``pydantic.ValidationError`` immediately (T-15-V5-01).

    Raises:
        httpx.HTTPStatusError: on 4xx/5xx after transport retries.
    """
    url = f"{base_url}/api/v1/teams?sportId={sport_id}&activeStatus=Y"
    await logger.adebug("mlb_fetch_teams", sport_id=sport_id, url=url)
    response = await client.get(url)
    response.raise_for_status()
    return MLBTeamsResponse.model_validate(response.json())
