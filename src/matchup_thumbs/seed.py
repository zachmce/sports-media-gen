"""Async seed CLI for the matchup-thumbs team registry.

Iterates ``LEAGUE_REGISTRY`` (provider-neutral: D-09/D-10/D-11) to populate
the ``teams``, ``team_aliases``, and league logo data in Postgres, and
pre-warms Redis under ``logo:{league}:{provider_id}`` and
``leaguelogo:{league}:{variant}``.

seed.py is intentionally provider-neutral: it receives canonical
``ProviderTeam`` / ``ProviderLogoShield`` objects from each provider's
methods and persists them without any ESPN-specific logic (D-11).  The NCAA
sportbanner SSRF gate lives in ``ESPNProvider.fetch_league_shield()`` (D-12).

Entrypoints
-----------
- ``uv run seed``           — registered uv project script (D-01)
- ``python -m matchup_thumbs.seed``
Both invoke the synchronous ``main()`` which drives the async core via
``asyncio.run``.

Graceful degradation (ESPN-05 / D-15)
--------------------------------------
When the provider is unreachable the seed logs the error and propagates the
exception so ``main()`` exits non-zero.  Existing Postgres rows are never
truncated — the last good registry keeps serving read traffic.

Security (T-02-03, T-02-04, T-02-06, T-14-04)
--------------------------------------
- ``seed_leagues`` input validated against ``KNOWN_LEAGUES`` (derived from
  ``LEAGUE_REGISTRY.keys()`` — the SSRF gate extends automatically to every
  future provider: D-10, criterion #4).
- All SQL uses parameterised ``%(name)s`` placeholders — never f-string SQL.
- structlog JSON renderer; no f-string log concatenation; no print().
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Sequence
from typing import Final

import anyio
import httpx
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .assets import get_placeholder_logo
from .espn.client import fetch_logo_bytes
from .providers.registry import KNOWN_LEAGUES, LEAGUE_REGISTRY
from .providers.types import ProviderTeam
from .settings import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Redis key prefix for league logos (D-04 / LGL-03)
# Namespace: leaguelogo:{league}:{variant}  (one word, no underscore)
# DISTINCT from the team logo namespace: logo:{league}:{provider_id}:{variant}
# ---------------------------------------------------------------------------

_LEAGUE_LOGO_KEY_PREFIX: Final[str] = "leaguelogo"

# ---------------------------------------------------------------------------
# League alias seed data (Phase 18 — LALIAS-03 / D-07)
#
# Keyed by canonical league slug; values are the raw (pre-normalisation) alias
# strings to insert into ``league_aliases``.  ``normalize_input`` is applied at
# insert time so ``"triple-a"`` → ``"triplea"`` etc.
#
# Do NOT include canonical slugs themselves (e.g. ``"milb-aaa"``) — Stage 1 of
# ``resolve_league`` matches ``leagues.slug`` directly without needing an alias.
#
# Do NOT add no-hyphen variants (e.g. ``"triplea"``): ``normalize_input`` strips
# hyphens, so ``"triple-a"`` and ``"triplea"`` normalise identically — adding
# both would create a duplicate row that ``ON CONFLICT (alias) DO NOTHING``
# silently absorbs, wasting a row count verification.
# ---------------------------------------------------------------------------

_LEAGUE_ALIASES: dict[str, list[str]] = {
    "ncaaf": ["college-football", "cfb"],
    "ncaab": ["college-basketball", "cbb"],
    "milb-aaa": ["triple-a", "aaa"],
    "milb-aa": ["double-a", "aa"],
    "milb-high-a": ["high-a"],
    "milb-single-a": ["single-a"],
    "milb-rookie": ["rookie"],
}


# ---------------------------------------------------------------------------
# Normalisation (single canonical implementation — resolver imports from here)
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


def generate_aliases(team: ProviderTeam) -> list[str]:
    """Generate normalised, de-duplicated aliases from provider team fields.

    Draws from: ``slug``, ``abbreviation``, ``location``, ``name``,
    ``display_name``, ``short_display_name``.

    Deliberately SKIPS any nickname field: for every major-league team the
    nickname field equals ``location``, so including it would produce a
    duplicate alias that hits ``ON CONFLICT DO NOTHING`` on every seed run.

    Returns a list preserving first-seen order with duplicates removed.
    """
    raw_sources = [
        team.slug,
        team.abbreviation,
        team.location,
        team.name,
        team.display_name,
        team.short_display_name,
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
        http_client: Shared ``httpx.AsyncClient`` for provider API + CDN calls.
        leagues:     League slugs to process.  Each must be in ``KNOWN_LEAGUES``.

    Raises:
        ValueError:  if any slug is not in ``KNOWN_LEAGUES`` (T-02-03).
        httpx.HTTPStatusError / Exception: propagated on provider failure so the
            caller exits non-zero (ESPN-05 / D-15).  Existing rows are
            never truncated.
    """
    # T-02-03: validate every slug against the registry-derived known set before
    # building URLs.  KNOWN_LEAGUES = frozenset(LEAGUE_REGISTRY.keys()) — the SSRF
    # gate extends automatically to every provider added in the future (D-10).
    unknown = [s for s in leagues if s not in KNOWN_LEAGUES]
    if unknown:
        raise ValueError(
            f"Unknown league slugs: {unknown!r}. "
            f"Supported leagues: {sorted(KNOWN_LEAGUES)}"
        )

    semaphore = asyncio.Semaphore(settings.espn_semaphore_size)

    for league_slug in leagues:
        provider = LEAGUE_REGISTRY[league_slug]
        await logger.ainfo("seed_league_start", league=league_slug)

        # --- League logo: ask the provider for a ProviderLogoShield, persist
        #     to Postgres, and pre-warm Redis (LGL-01, LGL-03, D-04, D-11). ---
        # The provider handles all provider-specific logic (ESPN distinct-href
        # check, NCAA sportbanner fallback, SSRF gate) — seed stays neutral.
        shield = await provider.fetch_league_shield(http_client, league_slug)

        # Persist logo_url + logo_variants to Postgres (D-05 idempotent UPDATE).
        # Leagues rows pre-exist from migration 0001; use UPDATE not INSERT.
        # Parameterized SQL only — never f-string the slug (T-11-03).
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE leagues
                SET logo_url = %(logo_url)s,
                    logo_variants = %(logo_variants)s,
                    sport_id = (SELECT id FROM sports WHERE slug = leagues.sport)
                WHERE slug = %(slug)s
                """,
                {
                    "slug": league_slug,
                    "logo_url": shield.logo_url,
                    "logo_variants": Jsonb(shield.variant_map),
                },
            )

        # Pre-warm Redis from the pre-fetched bytes in the shield.
        # The provider decides what to pre-fetch (ESPN distinct hrefs, NCAA single
        # fetch for both variants, or placeholder when no usable logo).
        pfx = f"{_LEAGUE_LOGO_KEY_PREFIX}:{league_slug}"
        if shield.bytes_default is not None:
            await redis.set(
                f"{pfx}:default".encode(),
                shield.bytes_default,
                ex=settings.logo_cache_ttl,
            )
        else:
            # No usable logo from provider — warm :default with placeholder
            await redis.set(
                f"{pfx}:default".encode(),
                get_placeholder_logo(),
                ex=settings.logo_cache_ttl,
            )

        if shield.bytes_dark is not None:
            # Provider gave distinct dark bytes — warm separately
            await redis.set(
                f"{pfx}:dark".encode(),
                shield.bytes_dark,
                ex=settings.logo_cache_ttl,
            )
        elif "dark" in shield.variant_map:
            # Dark is advertised in variant_map — warm :dark to keep the Redis
            # namespace consistent with what Postgres logo_variants advertises.
            # If bytes_default is available (provider fetched the bytes), reuse
            # them (NCAA case: single fetch shared by both variants).
            # If bytes_default is None (provider fetch failed), fall back to
            # placeholder so the cache namespace stays internally consistent
            # (12-04 belt-and-suspenders, AGENTS.md — degrade-don't-crash).
            dark_bytes = (
                shield.bytes_default
                if shield.bytes_default is not None
                else get_placeholder_logo()
            )
            await redis.set(
                f"{pfx}:dark".encode(),
                dark_bytes,
                ex=settings.logo_cache_ttl,
            )

        # --- Fetch team data from provider ---
        teams = await provider.fetch_teams(http_client, league_slug)

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

        await logger.ainfo(
            "seed_teams_fetched",
            league=league_slug,
            count=len(teams),
        )

        for team in teams:
            # Strip any leading '#' the provider may include before prepending exactly
            # one '#', preventing '##XXXXXX' double-prefix in the database (WR-04).
            # Color normalization stays in seed.py (D-05).
            primary_color = (
                f"#{team.primary_color.lstrip('#')}" if team.primary_color else None
            )
            secondary_color = (
                f"#{team.secondary_color.lstrip('#')}" if team.secondary_color else None
            )

            # --- Team upsert (D-03 idempotent, keyed on (league_id, slug)) ---
            # SQL stays parameterized: %(provider_id)s, %(provider)s — no f-string SQL
            # (T-14-04, T-15-INJ).  %(provider)s sourced from provider.provider_name
            # (Pitfall 3 — without it MiLB rows silently inherit server_default='espn').
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO teams
                            (league_id, slug, display_name, abbreviation,
                             primary_color, secondary_color, logo_url, provider_id,
                             logo_variants, provider)
                        VALUES (%(league_id)s, %(slug)s, %(display_name)s,
                                %(abbreviation)s, %(primary_color)s,
                                %(secondary_color)s, %(logo_url)s, %(provider_id)s,
                                %(logo_variants)s, %(provider)s)
                        ON CONFLICT (league_id, slug) DO UPDATE SET
                            display_name    = EXCLUDED.display_name,
                            abbreviation    = EXCLUDED.abbreviation,
                            primary_color   = EXCLUDED.primary_color,
                            secondary_color = EXCLUDED.secondary_color,
                            logo_url        = EXCLUDED.logo_url,
                            provider_id     = EXCLUDED.provider_id,
                            logo_variants   = EXCLUDED.logo_variants,
                            provider        = EXCLUDED.provider
                        RETURNING id
                        """,
                        {
                            "league_id": league_id,
                            "slug": team.slug,
                            "display_name": team.display_name,
                            "abbreviation": team.abbreviation,
                            "primary_color": primary_color,
                            "secondary_color": secondary_color,
                            "logo_url": team.logo_url,
                            "provider_id": team.provider_id,
                            "logo_variants": Jsonb(team.logo_variants),
                            "provider": provider.provider_name,
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
                    # extra_aliases arrive raw from the provider; normalize them
                    # so they obey the resolver invariant (aliases stored fully
                    # normalized via normalize_input). Without this the prefixed
                    # Rookie variants are dead rows the resolver never matches (WR-01).
                    extra_norm = [normalize_input(a) for a in team.extra_aliases]
                    for alias in generate_aliases(team) + extra_norm:
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
            cache_key = f"logo:{league_slug}:{team.provider_id}:default".encode()
            await redis.set(cache_key, logo_bytes, ex=settings.logo_cache_ttl)

        await logger.ainfo(
            "seed_league_complete",
            league=league_slug,
            teams=len(teams),
        )

    # Invalidate the rendered-image cache after seeding.  rendered:{...} keys are
    # keyed by request params + render_version — NOT by team data — so a re-seed
    # that changes team colours/logos would otherwise keep serving stale renders
    # until their TTL.  Clearing them here makes a re-seed take effect immediately
    # (removes the manual `redis-cli --scan --pattern 'rendered:*' | xargs DEL` step).
    flushed = 0
    async for render_key in redis.scan_iter(match="rendered:*"):
        await redis.delete(render_key)
        flushed += 1
    await logger.ainfo("seed_rendered_cache_flushed", keys=flushed)


async def _resolve_logo_bytes(
    http_client: httpx.AsyncClient,
    team: ProviderTeam,
    semaphore: asyncio.Semaphore,
    league_slug: str,
) -> bytes:
    """Resolve logo bytes via the D-10 fallback chain, rasterizing SVGs if needed.

    1. ``team.logo_url`` is the best href selected by the provider.
    2. ``fetch_logo_bytes`` fetches from the CDN (semaphore + jitter +
       tenacity retry on 429/5xx).
    3. ``rasterize_svg_if_needed`` is called off the event loop via
       ``anyio.to_thread.run_sync`` (D-19 seam A — pre-warms PNG bytes not SVG
       bytes into Redis; ESPN PNG bytes pass through unchanged, D-22).
    4. On any fetch error OR when no usable URL exists, fall back to the bundled
       placeholder PNG (``get_placeholder_logo()``).

    Rasterization only applies to the successfully-fetched bytes (success path).
    A fetch failure still returns the placeholder PNG — the placeholder is always
    valid PNG and if ever passed through rasterize_svg_if_needed it would be a
    no-op, but rasterization is deliberately kept only on the success path to
    avoid changing the failure behaviour.
    """
    if team.logo_url is None:
        return get_placeholder_logo()

    try:
        raw = await fetch_logo_bytes(
            http_client, team.logo_url, semaphore, settings.espn_jitter_max
        )
        # Rasterize off the event loop (Pitfall 1 — cairosvg is CPU-bound).
        # svg.py is imported lazily so seed.py loads even when libcairo2 is
        # absent (the import is deferred to _resolve_logo_bytes call time;
        # if libcairo2 is absent the OSError propagates here, which is caught
        # by the except block and returns the placeholder — graceful degradation).
        from .svg import rasterize_svg_if_needed

        return await anyio.to_thread.run_sync(rasterize_svg_if_needed, raw)
    except Exception as exc:
        await logger.aerror(
            "logo_fetch_failed",
            url=team.logo_url,
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
        description="Seed the matchup-thumbs team registry.",
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

    Drives the async core via ``asyncio.run``.  Translates provider/seed
    failures into a non-zero exit code.  Mirrors the ``def main() -> None``
    convention used by the existing ``api`` project script in ``main.py``.
    """
    try:
        asyncio.run(_amain())
    except SystemExit, KeyboardInterrupt:
        raise
    except Exception as exc:
        # Log the failure and exit non-zero (ESPN-05 / D-15)
        import structlog as _structlog

        _structlog.get_logger().error("seed_failed", error=str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
