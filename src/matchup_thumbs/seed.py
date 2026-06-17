"""Async seed CLI for the matchup-thumbs team registry.

Fetches team metadata for all six leagues from the public ESPN v2 sports API,
upserts ``teams`` + ``team_aliases`` into Postgres idempotently (D-03), fetches
logo bytes from the ESPN CDN and caches them in Redis under
``logo:{league}:{espn_id}``, and falls back to the bundled placeholder PNG when
a team has no usable logo (D-10 / ESPN-02).

Entrypoints
-----------
- ``uv run seed``           — registered uv project script (D-01)
- ``python -m matchup_thumbs.seed``
Both invoke the synchronous ``main()`` which drives the async core via
``asyncio.run``.

Graceful degradation (ESPN-05 / D-15)
--------------------------------------
When ESPN is unreachable the seed logs the error and propagates the exception so
``main()`` exits non-zero.  Existing Postgres rows are never truncated — the
last good registry keeps serving read traffic.

Security (T-02-03, T-02-04, T-02-06)
--------------------------------------
- ``seed_leagues`` input validated against the fixed six-slug set before any
  URL is constructed (no arbitrary endpoint / SSRF).
- All SQL uses parameterised ``%(name)s`` placeholders — never f-string SQL.
- structlog JSON renderer; no f-string log concatenation; no print().
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Sequence
from typing import Final

import httpx
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .assets import get_placeholder_logo
from .espn.client import (
    LEAGUE_ENDPOINTS,
    build_logo_variants,
    fetch_league_logo_data,
    fetch_logo_bytes,
    fetch_teams,
    select_logo_url,
)
from .espn.models import ESPNTeamEntry
from .settings import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Validation: the fixed set of supported league slugs (T-02-03)
# ---------------------------------------------------------------------------

KNOWN_LEAGUES: frozenset[str] = frozenset(LEAGUE_ENDPOINTS.keys())

# ---------------------------------------------------------------------------
# Redis key prefix for league logos (D-04 / LGL-03)
# Namespace: leaguelogo:{league}:{variant}  (one word, no underscore)
# DISTINCT from the team logo namespace: logo:{league}:{espn_id}:{variant}
# ---------------------------------------------------------------------------

_LEAGUE_LOGO_KEY_PREFIX: str = "leaguelogo"

# ---------------------------------------------------------------------------
# NCAA sportbanner mapping (T-i3r-01 SSRF gate)
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


# ---------------------------------------------------------------------------
# League logo helpers (LGL-03 / D-06)
# ---------------------------------------------------------------------------


def _has_usable_league_logo(variant_map: dict[str, str]) -> bool:
    """Return True if the variant map contains a real league logo.

    ESPN NCAA leagues return a ``logos`` array where both ``default`` and
    ``dark`` point to the same generic sport-icon URL.  A league logo is
    "usable" only when at least two distinct hrefs exist — structural
    same-href detection per D-06 and RESEARCH Pitfall 1.  No allowlist
    needed: if ESPN ever adds distinct NCAA icons the check flips correctly.
    """
    return len(set(variant_map.values())) > 1


# ---------------------------------------------------------------------------
# Normalisation (single canonical implementation — Plan 03 imports from here)
# ---------------------------------------------------------------------------


def normalize_input(raw: str) -> str:
    """Casefold and strip all non-alphanumeric characters.

    This is the **single canonical normaliser** for the project.  Apply it at
    alias seed time (here) and at resolver query time (resolver.py imports it).

    Examples::

        normalize_input("LA-Lakers")         -> "lalakers"
        normalize_input("Los Angeles Lakers") -> "losangeleslakers"
        normalize_input("LAL")               -> "lal"
        normalize_input("lakerz")            -> "lakerz"
    """
    return re.sub(r"[^a-z0-9]", "", raw.casefold())


# ---------------------------------------------------------------------------
# Alias generation (D-11)
# ---------------------------------------------------------------------------


def generate_aliases(team: ESPNTeamEntry) -> list[str]:
    """Generate normalised, de-duplicated aliases from ESPN team fields.

    Draws from: ``slug``, ``abbreviation``, ``location``, ``name``,
    ``displayName``, ``shortDisplayName``.

    Deliberately SKIPS ``nickname``: for every major-league team the nickname
    field equals ``location``, so including it would produce a duplicate alias
    that hits ``ON CONFLICT DO NOTHING`` on every seed run (Pitfall 2 /
    RESEARCH lines 488-502).

    Returns a list preserving first-seen order with duplicates removed.
    """
    raw_sources = [
        team.slug,
        team.abbreviation,
        team.location,
        team.name,
        team.displayName,
        team.shortDisplayName,
    ]
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_sources:
        norm = normalize_input(raw)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


# ---------------------------------------------------------------------------
# Core async seed runner
# ---------------------------------------------------------------------------


async def run(
    pool: AsyncConnectionPool,  # psycopg_pool generic — Any for pool type parameter
    redis: Redis,
    http_client: httpx.AsyncClient,
    leagues: Sequence[str],
) -> None:
    """Seed teams + aliases + logo bytes for the requested leagues.

    Args:
        pool:        Async psycopg3 connection pool (standalone or from lifespan).
        redis:       Async Redis client (``decode_responses=False``).
        http_client: Shared ``httpx.AsyncClient`` for ESPN API + CDN calls.
        leagues:     League slugs to process.  Each must be in ``KNOWN_LEAGUES``.

    Raises:
        ValueError:  if any slug is not in ``KNOWN_LEAGUES`` (T-02-03).
        httpx.HTTPStatusError / Exception: propagated on ESPN API failure so the
            caller exits non-zero (ESPN-05 / D-15).  Existing rows are
            never truncated.
    """
    # T-02-03: validate every slug against the fixed known set before building URLs
    unknown = [s for s in leagues if s not in KNOWN_LEAGUES]
    if unknown:
        raise ValueError(
            f"Unknown league slugs: {unknown!r}. "
            f"Supported leagues: {sorted(KNOWN_LEAGUES)}"
        )

    semaphore = asyncio.Semaphore(settings.espn_semaphore_size)

    for league_slug in leagues:
        await logger.ainfo("seed_league_start", league=league_slug)

        # --- League logo: fetch from ESPN core API, persist to Postgres,
        #     pre-warm Redis (LGL-01, LGL-03, D-04, D-05, D-06, T-11-01/T-11-03) ---
        # slug is already validated against KNOWN_LEAGUES above (T-02-03 / T-11-01)
        logos = await fetch_league_logo_data(
            http_client, settings.espn_core_api_base_url, league_slug
        )
        logo_url = select_logo_url(logos)
        variant_map = build_logo_variants(logos, league_slug, league_slug)

        # Persist logo_url + logo_variants to Postgres (D-05 idempotent UPDATE).
        # Leagues rows pre-exist from migration 0001; use UPDATE not INSERT (A5).
        # Parameterized SQL only — never f-string the slug (T-11-03).
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE leagues
                SET logo_url = %(logo_url)s,
                    logo_variants = %(logo_variants)s
                WHERE slug = %(slug)s
                """,
                {
                    "slug": league_slug,
                    "logo_url": logo_url,
                    "logo_variants": Jsonb(variant_map),
                },
            )

        # Pre-warm Redis under leaguelogo:{slug}:{variant} (D-04, D-06).
        # For pro leagues (distinct hrefs): warm both default and dark.
        # For NCAA (identical hrefs → not usable): warm :default with placeholder only.
        if _has_usable_league_logo(variant_map):
            for variant in set(variant_map.keys()) & {"default", "dark"}:
                href = variant_map[variant]
                try:
                    logo_bytes = await fetch_logo_bytes(
                        http_client, href, semaphore, settings.espn_jitter_max
                    )
                except Exception as exc:
                    await logger.aerror(
                        "league_logo_bytes_fetch_failed",
                        league=league_slug,
                        variant=variant,
                        url=href,
                        error=str(exc),
                    )
                    logo_bytes = get_placeholder_logo()
                cache_key = (
                    f"{_LEAGUE_LOGO_KEY_PREFIX}:{league_slug}:{variant}".encode()
                )
                await redis.set(cache_key, logo_bytes, ex=settings.logo_cache_ttl)
        else:
            # NCAA or any league with no usable distinct logo (D-06).
            if league_slug in _NCAA_SPORTBANNER_SPORTS:
                # Fetch the real per-sport shield from ncaa.com's public
                # sportbanner CDN (sanctioned second public source; see CLAUDE.md).
                # URL is built solely from a constant base + a constant-dict-derived
                # filename — no user/ESPN string reaches the URL (T-i3r-01 SSRF gate).
                sport = _NCAA_SPORTBANNER_SPORTS[league_slug]
                url = f"{settings.ncaa_sportbanner_base_url}/{sport}.png"
                try:
                    logo_bytes = await fetch_logo_bytes(
                        http_client, url, semaphore, settings.espn_jitter_max
                    )
                    # Update Postgres so logo_url/logo_variants reflect ncaa.com URL
                    # (parameterized %(...)s only — never f-string the slug, T-i3r-02).
                    ncaa_variant_map = {"default": url, "dark": url}
                    async with pool.connection() as conn, conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE leagues
                            SET logo_url = %(logo_url)s,
                                logo_variants = %(logo_variants)s
                            WHERE slug = %(slug)s
                            """,
                            {
                                "slug": league_slug,
                                "logo_url": url,
                                "logo_variants": Jsonb(ncaa_variant_map),
                            },
                        )
                except Exception as exc:
                    await logger.aerror(
                        "league_logo_bytes_fetch_failed",
                        league=league_slug,
                        url=url,
                        error=str(exc),
                    )
                    logo_bytes = get_placeholder_logo()
                # Warm BOTH :default and :dark with the same bytes (single fetch).
                # On failure: placeholder bytes (T-i3r-03 graceful degradation).
                for warm_variant in ("default", "dark"):
                    cache_key = (
                        f"{_LEAGUE_LOGO_KEY_PREFIX}:{league_slug}:{warm_variant}".encode()
                    )
                    await redis.set(cache_key, logo_bytes, ex=settings.logo_cache_ttl)
            else:
                # Unmapped not-usable league → placeholder (D-06).
                # Always warm :default.  Also warm :dark when advertised in variant_map
                # so the Redis namespace is internally consistent with what Postgres
                # logo_variants advertises (12-04 belt-and-suspenders, AGENTS.md).
                # This keeps select_league_logo_variant("dark") from cold-missing after
                # a DB-driven variant selection — the warm :dark key resolves to the
                # same placeholder image as :default (idempotent, no extra ESPN fetch).
                placeholder_bytes = get_placeholder_logo()
                for warm_variant in {"default"} | (
                    {"dark"} if "dark" in variant_map else set()
                ):
                    cache_key = (
                        f"{_LEAGUE_LOGO_KEY_PREFIX}:{league_slug}:{warm_variant}".encode()
                    )
                    await redis.set(
                        cache_key, placeholder_bytes, ex=settings.logo_cache_ttl
                    )

        # --- Fetch team JSON (ESPN failure propagates → no truncate) ---
        espn_response = await fetch_teams(
            http_client, settings.espn_base_url, league_slug
        )

        # Resolve league_id once per league
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM leagues WHERE slug = %(slug)s",
                {"slug": league_slug},
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(
                    f"League slug {league_slug!r} not found in leagues table. "
                    "Run migrations before seeding."
                )
            league_id: int = row[0]

        # --- Upsert teams + aliases ---
        teams_in_response = espn_response.sports[0].leagues[0].teams
        active_teams = [w.team for w in teams_in_response if w.team.isActive]

        await logger.ainfo(
            "seed_teams_fetched",
            league=league_slug,
            count=len(active_teams),
        )

        for team in active_teams:
            # Strip any leading '#' ESPN may include before prepending exactly
            # one '#', preventing '##XXXXXX' double-prefix in the database (WR-04).
            primary_color = f"#{team.color.lstrip('#')}" if team.color else None
            secondary_color = (
                f"#{team.alternateColor.lstrip('#')}" if team.alternateColor else None
            )
            logo_url = select_logo_url(team.logos)
            variant_map = build_logo_variants(team.logos, team.slug, league_slug)

            # --- Team upsert (D-03 idempotent, keyed on (league_id, slug)) ---
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO teams
                            (league_id, slug, display_name, abbreviation,
                             primary_color, secondary_color, logo_url, espn_id,
                             logo_variants)
                        VALUES (%(league_id)s, %(slug)s, %(display_name)s,
                                %(abbreviation)s, %(primary_color)s,
                                %(secondary_color)s, %(logo_url)s, %(espn_id)s,
                                %(logo_variants)s)
                        ON CONFLICT (league_id, slug) DO UPDATE SET
                            display_name    = EXCLUDED.display_name,
                            abbreviation    = EXCLUDED.abbreviation,
                            primary_color   = EXCLUDED.primary_color,
                            secondary_color = EXCLUDED.secondary_color,
                            logo_url        = EXCLUDED.logo_url,
                            espn_id         = EXCLUDED.espn_id,
                            logo_variants   = EXCLUDED.logo_variants
                        RETURNING id
                        """,
                        {
                            "league_id": league_id,
                            "slug": team.slug,
                            "display_name": team.displayName,
                            "abbreviation": team.abbreviation,
                            "primary_color": primary_color,
                            "secondary_color": secondary_color,
                            "logo_url": logo_url,
                            "espn_id": team.id,
                            "logo_variants": Jsonb(variant_map),
                        },
                    )
                    team_row = await cur.fetchone()
                    if team_row is None:
                        raise RuntimeError(
                            f"INSERT ... RETURNING id returned no row for team "
                            f"{team.slug!r} in league {league_slug!r}. "
                            "This should never happen — check DB constraints."
                        )
                    team_id: int = team_row[0]

                # --- Alias upsert (D-11 / D-12) ---
                async with conn.cursor() as cur:
                    for alias in generate_aliases(team):
                        await cur.execute(
                            """
                            INSERT INTO team_aliases (team_id, league_id, alias)
                            VALUES (%(team_id)s, %(league_id)s, %(alias)s)
                            ON CONFLICT (league_id, alias) DO NOTHING
                            """,
                            {
                                "team_id": team_id,
                                "league_id": league_id,
                                "alias": alias,
                            },
                        )
                        # D-12: log alias collisions (rowcount == 0 means skipped)
                        if cur.rowcount == 0:
                            await logger.awarning(
                                "alias_collision_skipped",
                                alias=alias,
                                league=league_slug,
                                team_slug=team.slug,
                            )

            # --- Logo bytes → Redis (D-09 / D-10 / ESPN-02) ---
            # Only the :default variant is pre-warmed; non-default variants are
            # fetched lazily by the loader on first request (D-10).
            logo_bytes = await _resolve_logo_bytes(
                http_client, team, semaphore, league_slug
            )
            cache_key = f"logo:{league_slug}:{team.id}:default".encode()
            await redis.set(cache_key, logo_bytes, ex=settings.logo_cache_ttl)

        await logger.ainfo(
            "seed_league_complete",
            league=league_slug,
            teams=len(active_teams),
        )


async def _resolve_logo_bytes(
    http_client: httpx.AsyncClient,
    team: ESPNTeamEntry,
    semaphore: asyncio.Semaphore,
    league_slug: str,
) -> bytes:
    """Resolve logo bytes via the D-10 fallback chain.

    1. ``select_logo_url`` picks the best href from the logos array.
    2. ``fetch_logo_bytes`` fetches from the ESPN CDN (semaphore + jitter +
       tenacity retry on 429/5xx).
    3. On any fetch error OR when no usable URL exists, fall back to the bundled
       placeholder PNG (``get_placeholder_logo()``).
    """
    logo_url = select_logo_url(team.logos)
    if logo_url is None:
        return get_placeholder_logo()

    try:
        return await fetch_logo_bytes(
            http_client, logo_url, semaphore, settings.espn_jitter_max
        )
    except Exception as exc:
        await logger.aerror(
            "logo_fetch_failed",
            url=logo_url,
            team=team.slug,
            league=league_slug,
            error=str(exc),
        )
        return get_placeholder_logo()


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------


async def _amain(argv: list[str] | None = None) -> None:
    """Async driver for the seed CLI.

    Builds standalone pool + Redis + httpx, parses args, and calls ``run()``.
    """
    parser = argparse.ArgumentParser(
        prog="seed",
        description="Seed the matchup-thumbs team registry from ESPN.",
    )
    parser.add_argument(
        "--leagues",
        default=settings.seed_leagues,
        help=(
            f"Comma-separated league slugs to seed (default: {settings.seed_leagues!r})"
        ),
    )
    parsed = parser.parse_args(argv)
    requested_leagues = [s.strip() for s in parsed.leagues.split(",") if s.strip()]

    conninfo = str(settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )

    async with AsyncConnectionPool(conninfo=conninfo, min_size=1, max_size=5) as pool:
        redis_client: Redis = Redis.from_url(
            str(settings.redis_url), decode_responses=False
        )
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.espn_request_timeout),
                transport=httpx.AsyncHTTPTransport(retries=2),
                follow_redirects=True,
            ) as http_client:
                await run(pool, redis_client, http_client, requested_leagues)
        finally:
            await redis_client.aclose()


def main() -> None:
    """Synchronous entrypoint for the ``seed`` uv project script (D-01).

    Drives the async core via ``asyncio.run``.  Translates ESPN/seed failures
    into a non-zero exit code.  Mirrors the ``def main() -> None`` convention
    used by the existing ``api`` project script in ``main.py``.
    """
    try:
        asyncio.run(_amain())
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        # Log the failure and exit non-zero (ESPN-05 / D-15)
        import structlog as _structlog

        _structlog.get_logger().error("seed_failed", error=str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
