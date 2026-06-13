"""2-stage fail-fast, league-scoped team resolver.

Given a raw user-supplied string and a target league, this module resolves
the input to a canonical team record via two progressively looser stages:

  Stage 1 — Exact match against ``team_aliases`` (normalized input matches alias
             exactly; league-scoped).
  Stage 2 — pg_trgm trigram fuzzy match (``similarity > threshold``), ordered
             by similarity descending, league-scoped.

A positive result is cached in Redis under ``resolve:{league}:{norm}`` (7-day
TTL).  On a cache *hit*, the full team row is re-fetched by id via
``_fetch_team_by_id`` so the return shape is identical to a DB hit — callers
(Phase 4 matchup routes) receive the same dict regardless of path.

A negative result (total miss) is cached under ``resolve_miss:{league}:{norm}``
(5-min TTL) to short-circuit repeat trigram scans on junk input.

Security
--------
- T-02-08: All SQL uses ``%s`` positional parameters — never f-string SQL.
- T-02-09: Input longer than 100 characters is rejected before normalisation;
  negative cache then short-circuits repeated junk scans.
- T-02-10: Input is normalised (alphanumerics only) before keying into Redis;
  every key is also scoped by league slug.
- T-02-11: Every SQL stage filters by ``league_id`` sub-select; cross-league
  isolation verified by ``test_resolver_scope``.
"""

from __future__ import annotations

from typing import Any

import structlog
from psycopg import rows as pg_rows
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .seed import normalize_input
from .settings import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Column list shared across all team-row queries.
# Define once so DB-hit and cache-re-fetch paths return identical dict keys.
# ---------------------------------------------------------------------------

_TEAM_COLUMNS = (
    "t.id, t.league_id, t.slug, t.display_name, "
    "t.abbreviation, t.primary_color, t.secondary_color, "
    "t.logo_url, t.espn_id"
)

# Maximum raw input length accepted before treating as a miss (T-02-09).
_MAX_INPUT_LEN = 100


# ---------------------------------------------------------------------------
# Private stage helpers
# ---------------------------------------------------------------------------


async def _query_exact(
    pool: AsyncConnectionPool[Any],
    league_slug: str,
    alias: str,
) -> dict[str, Any] | None:
    """Stage 1 / Stage 2: exact alias match, league-scoped.

    Parameterised only — never string-formatted (T-02-08).
    """
    sql = f"""
        SELECT {_TEAM_COLUMNS}
        FROM teams t
        JOIN team_aliases ta ON ta.team_id = t.id
        WHERE ta.league_id = (SELECT id FROM leagues WHERE slug = %s)
          AND ta.alias = %s
        LIMIT 1
    """
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=pg_rows.dict_row) as cur,
    ):
        await cur.execute(sql, (league_slug, alias))
        row = await cur.fetchone()
        return dict(row) if row is not None else None


async def _query_trigram(
    pool: AsyncConnectionPool[Any],
    league_slug: str,
    norm: str,
) -> dict[str, Any] | None:
    """Stage 3: pg_trgm fuzzy match above ``resolve_similarity_threshold``.

    Selects the same ``_TEAM_COLUMNS`` plus a transient ``sim`` column used
    only for ordering — callers receive the dict without ``sim``.  The threshold
    comes from settings (no magic literal), satisfying T-02-08 / AGENTS.md
    no-magic-numbers rule.
    """
    sql = f"""
        SELECT {_TEAM_COLUMNS},
               similarity(ta.alias, %s) AS sim
        FROM teams t
        JOIN team_aliases ta ON ta.team_id = t.id
        WHERE ta.league_id = (SELECT id FROM leagues WHERE slug = %s)
          AND similarity(ta.alias, %s) > %s
        ORDER BY sim DESC
        LIMIT 1
    """
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=pg_rows.dict_row) as cur,
    ):
        await cur.execute(
            sql,
            (norm, league_slug, norm, settings.resolve_similarity_threshold),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        # Drop the transient similarity score — not part of the contract.
        row_dict = dict(row)
        row_dict.pop("sim", None)
        return row_dict


async def _fetch_team_by_id(
    pool: AsyncConnectionPool[Any],
    team_id: int,
) -> dict[str, Any] | None:
    """Re-fetch the full team row by primary key.

    Used on positive-cache hits to return the SAME dict shape as a DB hit.
    Returns None when the team has been deleted since caching (stale id).
    """
    sql = f"SELECT {_TEAM_COLUMNS} FROM teams t WHERE t.id = %s LIMIT 1"
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=pg_rows.dict_row) as cur,
    ):
        await cur.execute(sql, (team_id,))
        row = await cur.fetchone()
        return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Public resolver entry-point
# ---------------------------------------------------------------------------


async def resolve(
    league: str,
    raw_input: str,
    pool: AsyncConnectionPool[Any],
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
) -> dict[str, Any] | None:
    """Resolve raw user input to a canonical team dict, league-scoped.

    Return contract (locked — Phase 4 consumers depend on this shape):
        None — total miss (all three stages exhausted + negative cache set).
        dict — full team row with keys:
            id, league_id, slug, display_name, abbreviation,
            primary_color, secondary_color, logo_url, espn_id.

    Both the DB-hit path AND the positive-cache-hit path return this identical
    shape (cache hit re-fetches the full row via ``_fetch_team_by_id``).

    Args:
        league:    League slug (e.g. ``"nba"``), used for league-scope filters
                   and Redis key construction.
        raw_input: Raw user-supplied string (e.g. ``"lakerz"``).
        pool:      Async psycopg3 connection pool.
        redis:     Async Redis client (``decode_responses=False``).

    Returns:
        Full team-row dict or None.
    """
    # T-02-09: reject overlong input before normalization to bound trigram cost.
    if len(raw_input) > _MAX_INPUT_LEN:
        await logger.awarning(
            "resolver_input_too_long",
            league=league,
            length=len(raw_input),
        )
        miss_key_long = f"resolve_miss:{league}:{raw_input[:_MAX_INPUT_LEN]}".encode()
        await redis.set(miss_key_long, b"miss", ex=settings.resolve_negative_ttl)
        return None

    norm = normalize_input(raw_input)
    cache_key = f"resolve:{league}:{norm}".encode()
    miss_key = f"resolve_miss:{league}:{norm}".encode()

    # ------------------------------------------------------------------
    # Positive cache check — re-fetch full row to honour the return contract.
    # decode_responses=False guarantees bytes at runtime; cast for mypy.
    # ------------------------------------------------------------------
    cached = await redis.get(cache_key)
    if cached is not None:
        # At runtime (decode_responses=False) this is always bytes.
        cached_bytes: bytes = cached if isinstance(cached, bytes) else cached.encode()
        team_id = int(cached_bytes.decode())
        row = await _fetch_team_by_id(pool, team_id)
        if row is not None:
            return row
        # Stale cached id (team deleted) — clear key and fall through.
        await redis.delete(cache_key)

    # ------------------------------------------------------------------
    # Negative cache check — short-circuit repeat trigram scans (T-02-09).
    # ------------------------------------------------------------------
    neg_cached = await redis.get(miss_key)
    if neg_cached is not None:
        return None

    # ------------------------------------------------------------------
    # Stage 1: exact match on normalized input.
    # ------------------------------------------------------------------
    row = await _query_exact(pool, league, norm)

    # ------------------------------------------------------------------
    # Stage 2: trigram fuzzy match.
    # (The former Stage 2 exact-match on casefolded input was a no-op:
    # normalize_input already produces norm, so it issued the same SQL
    # as Stage 1 and wasted a DB round-trip.  Aliases are always stored
    # fully normalized via generate_aliases(), so no intermediate form
    # can match that Stage 1 would miss.)
    # ------------------------------------------------------------------
    if row is None:
        row = await _query_trigram(pool, league, norm)

    # ------------------------------------------------------------------
    # Cache the outcome.
    # ------------------------------------------------------------------
    if row is not None:
        await redis.set(
            cache_key,
            str(row["id"]).encode(),
            ex=settings.resolve_positive_ttl,
        )
        return row

    # Total miss — set negative cache to blunt repeat trigram scans.
    await logger.awarning(
        "resolver_miss",
        league=league,
        raw_input=raw_input,
        norm=norm,
    )
    await redis.set(miss_key, b"miss", ex=settings.resolve_negative_ttl)
    return None
