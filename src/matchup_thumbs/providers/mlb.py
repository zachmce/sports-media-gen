"""MLB Stats API concrete DataProvider implementation.

Wraps the ``mlb/client.py`` fetch helper and maps MLB Stats API fields to the
provider-neutral ``ProviderTeam`` / ``ProviderLogoShield`` canonical types.

MiLB sportId SSRF gate (T-i3r-01)
------------------------------------
``_MILB_SPORT_IDS``: a **fixed module-level mapping** from the already
KNOWN_LEAGUES-validated slug to an integer sportId.  No user-supplied string
ever reaches the URL — the dict lookup is the gate.  Mirrors
``_NCAA_SPORTBANNER_SPORTS`` in ``providers/espn.py``.
"""

from __future__ import annotations

import re
from typing import Final

import httpx
import structlog

from ..mlb.client import fetch_mlb_teams
from ..settings import settings
from .types import ProviderLogoShield, ProviderTeam

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# MiLB level→sportId mapping (T-i3r-01 SSRF gate — mirrors _NCAA_SPORTBANNER_SPORTS)
# ---------------------------------------------------------------------------
# Fixed mapping from KNOWN_LEAGUES-validated slug to the MLB Stats API sportId.
# The URL is built only from settings.mlb_statsapi_base_url (a constant) +
# the integer from this dict.  No user/API string ever reaches the URL — the
# dict lookup is the gate: unknown slug → KeyError, no URL construction.
_MILB_SPORT_IDS: Final[dict[str, int]] = {
    "milb-aaa": 11,
    "milb-aa": 12,
    "milb-high-a": 13,
    "milb-single-a": 14,
}


def _derive_mlb_slug(location_name: str, team_name: str) -> str:
    """Derive a kebab-case slug from MLB locationName + teamName.

    Verified zero intra-level collisions across all 4 levels (120 teams).
    Mirrors the normalize style used in seed.py::normalize_input for
    consistency — but produces hyphen-separated (slug) not stripped (alias).
    """
    raw = f"{location_name} {team_name}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


class MLBStatsProvider:
    """Concrete DataProvider for the free, key-free MLB Stats API.

    Structurally satisfies the ``DataProvider`` Protocol (D-01) — no
    inheritance required.  Stateless and cheap; one shared singleton in
    ``providers/registry.py``.

    The shared ``httpx.AsyncClient`` is always passed as a parameter (D-02).

    Attribute ``provider_name`` is read by seed.py to populate the
    ``teams.provider`` discriminator column (Pitfall 3 / RESEARCH open question 1).
    """

    provider_name: str = "mlb"

    def list_leagues(self) -> list[str]:
        """Return the 4 MiLB level slugs this provider covers."""
        return list(_MILB_SPORT_IDS.keys())

    async def fetch_teams(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> list[ProviderTeam]:
        """Fetch active MiLB teams for a level and return canonical ProviderTeam models.

        Args:
            client:      Shared ``httpx.AsyncClient`` (D-02).
            league_slug: One of the 4 supported slugs (KNOWN_LEAGUES gate applied
                         upstream by seed.py before this call).

        Returns:
            List of active ``ProviderTeam`` instances (active=True only).

        Raises:
            KeyError: if ``league_slug`` is not in ``_MILB_SPORT_IDS`` (SSRF gate).
            httpx.HTTPStatusError: on MLB Stats API 4xx/5xx.
            pydantic.ValidationError: on unexpected MLB API response schema drift.
        """
        # KeyError on unknown slug (SSRF gate — never build URL before this lookup)
        sport_id = _MILB_SPORT_IDS[league_slug]

        response = await fetch_mlb_teams(
            client, settings.mlb_statsapi_base_url, sport_id
        )
        return [
            ProviderTeam(
                provider_id=str(entry.id),  # int → str (Pitfall 8)
                slug=_derive_mlb_slug(entry.locationName, entry.teamName),
                display_name=entry.name,
                abbreviation=entry.abbreviation,
                short_display_name=entry.teamName,  # mascot (Pitfall 7)
                location=entry.locationName,
                name=entry.teamName,  # mascot (Pitfall 7)
                primary_color=None,  # MLB API has no colors (D-14)
                secondary_color=None,
                logo_url=(
                    f"{settings.mlb_spots_base_url}/v1/team/{entry.id}/spots/500"
                ),
                logo_variants={
                    "svg": f"{settings.mlb_logos_base_url}/{entry.id}.svg"
                },
                is_active=entry.active,
            )
            for entry in response.teams
            if entry.active
        ]

    async def fetch_league_shield(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> ProviderLogoShield:
        """MiLB affiliate levels have no standalone league logo in the MLB Stats API.

        Returns an empty shield; seed.py will warm the league logo slot with the
        placeholder PNG.  Acceptable for v2.0 (MISVG-FUT-01 / MICOL-FUT-01 deferred).
        """
        await logger.adebug("mlb_league_shield_empty", league=league_slug)
        return ProviderLogoShield(
            logo_url=None,
            variant_map={},
            bytes_default=None,
            bytes_dark=None,
        )
