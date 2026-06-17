"""ESPN concrete DataProvider implementation.

Wraps the existing ``espn/client.py`` fetch helpers (composition, not
rewrite) and maps ESPN's camelCase ``ESPNTeamEntry`` fields to the
provider-neutral ``ProviderTeam`` / ``ProviderLogoShield`` canonical types.

NCAA sportbanner SSRF gate (T-i3r-01)
--------------------------------------
ESPN returns identical ``default`` and ``dark`` hrefs for ncaaf/ncaab — a
real per-sport shield is not usable from ESPN for these leagues.  The
fallback fetches from the ncaa.com sportbanner CDN (sanctioned second public
source; see CLAUDE.md) using:

- ``_NCAA_SPORTBANNER_SPORTS``: a **fixed module-level mapping** from the
  already KNOWN_LEAGUES-validated slug to a sport filename.  No user-supplied
  or ESPN-supplied string ever reaches the URL.
- ``settings.ncaa_sportbanner_base_url``: a **constant** base URL from config.

The dict-lookup is the gate: an unmapped slug never triggers a fetch.
This satisfies T-i3r-01 verbatim.
"""

from __future__ import annotations

import asyncio
from typing import Final

import httpx
import structlog

from ..espn.client import (
    LEAGUE_ENDPOINTS,
    build_logo_variants,
    fetch_league_logo_data,
    fetch_logo_bytes,
    select_logo_url,
)
from ..espn.client import (
    fetch_teams as _espn_fetch_teams,
)
from ..settings import settings
from .types import ProviderLogoShield, ProviderTeam

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# NCAA sportbanner mapping (T-i3r-01 SSRF gate — moved verbatim from seed.py)
# ---------------------------------------------------------------------------
# Fixed mapping from KNOWN_LEAGUES-validated slug to the ncaa.com sport
# filename.  The URL is built only from settings.ncaa_sportbanner_base_url
# (a constant) + the filename from this dict (also a constant).  No
# user-supplied or ESPN-supplied string ever reaches the URL — the dict
# lookup is the gate: unmapped slug → placeholder, no fetch.
_NCAA_SPORTBANNER_SPORTS: Final[dict[str, str]] = {
    "ncaaf": "football",
    "ncaab": "basketball",
}


def _has_usable_league_logo(variant_map: dict[str, str]) -> bool:
    """Return True if the variant map contains a real league logo.

    ESPN NCAA leagues return a ``logos`` array where both ``default`` and
    ``dark`` point to the same generic sport-icon URL.  A league logo is
    "usable" only when at least two distinct hrefs exist — structural
    same-href detection per D-06 and RESEARCH Pitfall 1.  No allowlist
    needed: if ESPN ever adds distinct NCAA icons the check flips correctly.
    """
    return len(set(variant_map.values())) > 1


class ESPNProvider:
    """Concrete DataProvider that wraps the existing espn/client.py helpers.

    Structurally satisfies the ``DataProvider`` Protocol (D-01) — no
    inheritance required; mypy validates compatibility at assignment sites.

    The provider is intentionally stateless and cheap to construct.  A single
    shared instance is created in ``providers/registry.py``; callers should
    use that singleton rather than constructing a new instance.

    The shared ``httpx.AsyncClient`` is always passed as a parameter — this
    class never owns or creates an HTTP client (D-02).
    """

    def list_leagues(self) -> list[str]:
        """Return the list of league slugs this provider covers.

        Returns the ordered keys of ``LEAGUE_ENDPOINTS`` — the authoritative
        source for ESPN-supported slugs.
        """
        return list(LEAGUE_ENDPOINTS.keys())

    async def fetch_teams(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> list[ProviderTeam]:
        """Fetch active teams for a league and return canonical ProviderTeam models.

        Delegates to ``espn/client.fetch_teams`` for the ESPN API call and
        ``ESPNTeamsResponse`` parse, then maps each active ``ESPNTeamEntry``
        to a ``ProviderTeam`` — translating camelCase ESPN fields to snake_case
        canonical fields (D-04/D-05).

        Inactive teams (``isActive=False``) are filtered out identically to the
        pre-refactor seed behaviour.

        Args:
            client:      Shared ``httpx.AsyncClient`` (caller-supplied, D-02).
            league_slug: One of the six supported slugs (KNOWN_LEAGUES gate
                         applied upstream by seed.py::run() before this call).

        Returns:
            List of active ``ProviderTeam`` instances with provider-neutral fields.

        Raises:
            KeyError: if ``league_slug`` is not in ``LEAGUE_ENDPOINTS`` (SSRF gate).
            httpx.HTTPStatusError: on ESPN API 4xx/5xx after transport retries.
            pydantic.ValidationError: on unexpected ESPN response schema drift.
        """
        response = await _espn_fetch_teams(
            client, settings.espn_base_url, league_slug
        )
        teams = response.sports[0].leagues[0].teams
        return [
            ProviderTeam(
                provider_id=w.team.id,
                slug=w.team.slug,
                display_name=w.team.displayName,
                abbreviation=w.team.abbreviation,
                short_display_name=w.team.shortDisplayName,
                location=w.team.location,
                name=w.team.name,
                primary_color=w.team.color,
                secondary_color=w.team.alternateColor,
                logo_url=select_logo_url(w.team.logos),
                logo_variants=build_logo_variants(
                    w.team.logos, w.team.slug, league_slug
                ),
                is_active=w.team.isActive,
            )
            for w in teams
            if w.team.isActive
        ]

    async def fetch_league_shield(
        self,
        client: httpx.AsyncClient,
        league_slug: str,
    ) -> ProviderLogoShield:
        """Fetch league shield (logo) data and return a canonical ProviderLogoShield.

        For ESPN leagues with distinct default/dark logo hrefs: fetches both
        variants from the ESPN CDN.

        For NCAA leagues (ncaaf/ncaab) where ESPN returns identical default/dark
        hrefs — indicating no usable per-sport shield — falls back to the ncaa.com
        sportbanner CDN.  The fallback URL is built ONLY from:

        1. ``settings.ncaa_sportbanner_base_url``  — a constant from config.
        2. ``_NCAA_SPORTBANNER_SPORTS[league_slug]`` — a fixed module-level dict.

        No user-supplied or ESPN-supplied string ever reaches the URL; the dict
        lookup is the SSRF gate (T-i3r-01).  An unmapped slug falls through to
        the placeholder path with no URL construction and no fetch.

        Args:
            client:      Shared ``httpx.AsyncClient`` (caller-supplied, D-02).
            league_slug: One of the six supported slugs (KNOWN_LEAGUES gate
                         applied upstream by seed.py::run() before this call).

        Returns:
            ``ProviderLogoShield`` carrying ``logo_url``, ``variant_map``,
            ``bytes_default`` (pre-fetched), and ``bytes_dark`` (``None`` when
            the dark variant is identical to default or unavailable).
        """
        semaphore = asyncio.Semaphore(settings.espn_semaphore_size)

        # Fetch ESPN league logo metadata
        logos = await fetch_league_logo_data(
            client, settings.espn_core_api_base_url, league_slug
        )
        logo_url = select_logo_url(logos)
        variant_map = build_logo_variants(logos, league_slug, league_slug)

        if _has_usable_league_logo(variant_map):
            # ESPN provides distinct default/dark hrefs — use them directly.
            bytes_default: bytes | None = None
            bytes_dark: bytes | None = None

            if "default" in variant_map:
                try:
                    bytes_default = await fetch_logo_bytes(
                        client,
                        variant_map["default"],
                        semaphore,
                        settings.espn_jitter_max,
                    )
                except Exception as exc:
                    await logger.aerror(
                        "league_shield_default_fetch_failed",
                        league=league_slug,
                        url=variant_map["default"],
                        error=str(exc),
                    )

            if "dark" in variant_map:
                try:
                    bytes_dark = await fetch_logo_bytes(
                        client,
                        variant_map["dark"],
                        semaphore,
                        settings.espn_jitter_max,
                    )
                except Exception as exc:
                    await logger.aerror(
                        "league_shield_dark_fetch_failed",
                        league=league_slug,
                        url=variant_map["dark"],
                        error=str(exc),
                    )

            return ProviderLogoShield(
                logo_url=logo_url,
                variant_map=variant_map,
                bytes_default=bytes_default,
                bytes_dark=bytes_dark,
            )

        # ESPN NCAA leagues return identical hrefs — check for ncaa.com fallback.
        # SSRF gate: the sport filename comes ONLY from _NCAA_SPORTBANNER_SPORTS;
        # base URL is the constant setting.  No user/ESPN string reaches the URL.
        if league_slug in _NCAA_SPORTBANNER_SPORTS:
            sport = _NCAA_SPORTBANNER_SPORTS[league_slug]
            ncaa_url = f"{settings.ncaa_sportbanner_base_url}/{sport}.png"
            ncaa_variant_map = {"default": ncaa_url, "dark": ncaa_url}

            await logger.adebug(
                "league_shield_ncaa_fallback",
                league=league_slug,
                url=ncaa_url,
            )

            # Single fetch — both default and dark share the same bytes
            ncaa_bytes: bytes | None = None
            try:
                ncaa_bytes = await fetch_logo_bytes(
                    client,
                    ncaa_url,
                    semaphore,
                    settings.espn_jitter_max,
                )
            except Exception as exc:
                await logger.aerror(
                    "league_shield_ncaa_fetch_failed",
                    league=league_slug,
                    url=ncaa_url,
                    error=str(exc),
                )

            return ProviderLogoShield(
                logo_url=ncaa_url,
                variant_map=ncaa_variant_map,
                bytes_default=ncaa_bytes,
                # same as default — caller warms both from bytes_default
                bytes_dark=None,
            )

        # Unmapped not-usable league — no fetch, return empty shield.
        await logger.adebug(
            "league_shield_no_usable_logo",
            league=league_slug,
        )
        return ProviderLogoShield(
            logo_url=logo_url,
            variant_map=variant_map,
            bytes_default=None,
            bytes_dark=None,
        )
