"""3-stage fail-fast, league-scoped team resolver.

Given a raw user-supplied string and a target league, this module resolves
the input to a canonical team record via three progressively looser stages:

  Stage 1 — Exact alias match against ``team_aliases`` on the normalized
             input (``normalize_input`` strips punctuation and casefolds;
             league-scoped).
  Stage 2 — Subsumed by Stage 1: because the input is normalized *before*
             querying, the casefolded-exact pass issues the identical SQL as
             Stage 1, so the two collapse into a single query (kept as a named
             step for parity with the alias-generation pipeline).
  Stage 3 — pg_trgm trigram fuzzy match (``similarity > threshold``), ordered
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
- T-02-09: Input longer than 100 characters is rejected before normalisation
  with an early return (no Redis write needed; the branch never reaches the
  trigram stage).
- T-02-10: Input is normalised (alphanumerics only) before keying into Redis;
  every key is also scoped by league slug.
- T-02-11: Every SQL stage filters by ``league_id`` sub-select; cross-league
  isolation verified by ``test_resolver_scope``.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import structlog
from psycopg import rows as pg_rows
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .providers.registry import KNOWN_LEAGUES
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
    "t.logo_url, t.provider_id, t.logo_variants"
)

# Maximum raw input length accepted before treating as a miss (T-02-09).
_MAX_INPUT_LEN = 100


class LeagueResolution(NamedTuple):
    """Typed result returned by ``resolve_league``.

    Fields
    ------
    slug:
        Canonical league slug (always a member of ``KNOWN_LEAGUES``).
    sport:
        Sport slug from the ``sports`` table FK join (e.g. ``"baseball"``).
    """

    slug: str
    sport: str


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
# League resolver helpers (Phase 18 — LALIAS-02)
# ---------------------------------------------------------------------------


async def _query_league_exact(
    pool: AsyncConnectionPool[Any],
    alias_norm: str,
    slug_match: str,
) -> tuple[str, str] | None:
    """Stage 1 / Stage 2: exact match, globally scoped.

    Matches ``alias_norm`` (the fully normalized input) against
    ``league_aliases.alias`` OR ``slug_match`` (the casefolded raw input with
    hyphens preserved) against ``leagues.slug``.  The two forms differ because
    canonical slugs may contain hyphens (e.g. ``milb-aaa``) that
    ``normalize_input`` strips — so the direct-slug branch MUST use the
    hyphen-preserving casefolded form, otherwise every hyphenated league slug
    (``milb-aaa``/``milb-aa``/``milb-high-a``/``milb-a``/``milb-rookie``/
    ``milb-winter``/``milb-independent``) silently fails the direct match and
    404s (CR-01).  The ``sports`` table is joined via ``leagues.sport_id`` so
    both slug and sport return in one query.

    ``ORDER BY l.id`` makes the ``LIMIT 1`` tie-break deterministic (WR-01).

    Parameterised only — never string-formatted (T-02-08, T-18-INJ).
    """
    sql = """
        SELECT l.slug, s.slug AS sport
        FROM leagues l
        JOIN sports s ON s.id = l.sport_id
        WHERE l.id IN (
            SELECT la.league_id FROM league_aliases la WHERE la.alias = %s
        ) OR l.slug = %s
        ORDER BY l.id
        LIMIT 1
    """
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=pg_rows.dict_row) as cur,
    ):
        await cur.execute(sql, (alias_norm, slug_match))
        row = await cur.fetchone()
        if row is None:
            return None
        return (row["slug"], row["sport"])


async def _query_league_trigram(
    pool: AsyncConnectionPool[Any],
    norm: str,
) -> tuple[str, str] | None:
    """Stage 3: pg_trgm fuzzy match over ``league_aliases.alias`` (GIN-indexed).

    Queries ``league_aliases.alias`` ONLY — not ``leagues.slug`` — because only
    the alias column carries the GIN trigram index (``ix_league_aliases_alias_trgm``
    from migration 0007).  Stage 1 already handles direct slug matches; Stage 3
    must not scan unindexed columns.

    The transient ``sim`` score is used for ordering only and is dropped from the
    returned tuple (mirrors ``_query_trigram``).

    Parameterised only — never string-formatted (T-02-08, T-18-INJ).
    """
    sql = """
        SELECT l.slug, s.slug AS sport,
               similarity(la.alias, %s) AS sim
        FROM league_aliases la
        JOIN leagues l ON l.id = la.league_id
        JOIN sports s ON s.id = l.sport_id
        WHERE similarity(la.alias, %s) > %s
        ORDER BY sim DESC
        LIMIT 1
    """
    async with (
        pool.connection() as conn,
        conn.cursor(row_factory=pg_rows.dict_row) as cur,
    ):
        await cur.execute(
            sql,
            (norm, norm, settings.resolve_similarity_threshold),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        # Drop the transient similarity score — not part of the return contract.
        return (row["slug"], row["sport"])


# ---------------------------------------------------------------------------
# Public league resolver entry-point (Phase 18 — LALIAS-02)
# ---------------------------------------------------------------------------


async def resolve_league(
    raw_input: str,
    pool: AsyncConnectionPool[Any],
    redis: Redis,  # bare Redis (redis-py 8.0 is not a generic class at runtime)
) -> LeagueResolution | None:
    """Resolve raw user input to a canonical league slug + sport, globally scoped.

    Return contract:
        None — total miss (all stages exhausted + negative cache set),
               overlong input, or resolved slug not in ``KNOWN_LEAGUES``.
        LeagueResolution — ``(slug, sport)`` where ``slug`` is a member of
               ``KNOWN_LEAGUES`` and ``sport`` is from ``sports.slug`` via the
               ``leagues.sport_id`` FK join.

    Unlike ``resolve()`` (which is league-scoped for teams), this function has
    no outer league scope — league aliases are globally unique (Phase 17 D-07
    ``UNIQUE(alias)``), so no scope parameter is needed.

    Redis keys are also global:
      - positive: ``leagueresolve:{norm}`` (value: ``b"slug:sport"``)
      - negative: ``leagueresolve_miss:{norm}`` (value: ``b"miss"``)

    Security
    --------
    - T-18-INJ: All SQL in helpers uses ``%s`` positional parameters.
    - T-18-DOS: Input longer than ``_MAX_INPUT_LEN`` is rejected before
      normalization (same as ``resolve()``'s T-02-09 guard).
    - T-18-SSRF: Resolved canonical slug is checked against ``KNOWN_LEAGUES``
      before being returned or cached positively.

    Args:
        raw_input: Raw user-supplied string (e.g. ``"triple-a"`` or ``"mlb"``).
        pool:      Async psycopg3 connection pool.
        redis:     Async Redis client (``decode_responses=False``).

    Returns:
        ``LeagueResolution(slug, sport)`` or None.
    """
    # T-18-DOS / T-02-09 parity: reject overlong input before normalization.
    if len(raw_input) > _MAX_INPUT_LEN:
        await logger.awarning(
            "resolve_league_input_too_long",
            length=len(raw_input),
        )
        return None

    norm = normalize_input(raw_input)
    # Hyphen-preserving casefolded form for the direct ``leagues.slug`` match
    # (CR-01): canonical slugs like ``milb-aaa`` keep their hyphens, which
    # ``normalize_input`` strips.  The alias branch still uses ``norm``.
    slug_match = raw_input.strip().casefold()
    cache_key = f"leagueresolve:{norm}".encode()
    miss_key = f"leagueresolve_miss:{norm}".encode()

    # ------------------------------------------------------------------
    # Positive cache check — decode "slug:sport" pair.
    # decode_responses=False guarantees bytes at runtime; cast for mypy.
    # ------------------------------------------------------------------
    cached = await redis.get(cache_key)
    if cached is not None:
        cached_bytes: bytes = cached if isinstance(cached, bytes) else cached.encode()
        parts = cached_bytes.decode().split(":", 1)
        if len(parts) == 2 and parts[0] in KNOWN_LEAGUES:
            return LeagueResolution(slug=parts[0], sport=parts[1])
        # Stale or malformed positive cache entry — delete and fall through.
        await redis.delete(cache_key)

    # ------------------------------------------------------------------
    # Negative cache check — short-circuit repeat trigram scans (T-18-DOS).
    # ------------------------------------------------------------------
    neg_cached = await redis.get(miss_key)
    if neg_cached is not None:
        return None

    # ------------------------------------------------------------------
    # Stage 1: exact match on normalized input (alias OR canonical slug).
    # ------------------------------------------------------------------
    result = await _query_league_exact(pool, norm, slug_match)

    # ------------------------------------------------------------------
    # Stage 3: trigram fuzzy match over league_aliases.alias (GIN index).
    # (Stage 2 collapses into Stage 1: normalize_input produces the same
    # form as stored aliases, so a separate normalized-exact pass is
    # redundant — identical to the team resolver's collapse rationale.)
    # ------------------------------------------------------------------
    if result is None:
        result = await _query_league_trigram(pool, norm)

    # ------------------------------------------------------------------
    # Cache the outcome.
    # ------------------------------------------------------------------
    if result is not None:
        slug, sport = result
        # T-18-SSRF belt-and-suspenders: the resolved slug must be in
        # KNOWN_LEAGUES before being used downstream (mirrors lines 192-194).
        if slug not in KNOWN_LEAGUES:
            await logger.awarning("resolve_league_unknown_slug", slug=slug)
            await redis.set(miss_key, b"miss", ex=settings.resolve_negative_ttl)
            return None
        lr = LeagueResolution(slug=slug, sport=sport)
        await redis.set(
            cache_key,
            f"{slug}:{sport}".encode(),
            ex=settings.resolve_positive_ttl,
        )
        return lr

    # Total miss — set negative cache to blunt repeat trigram scans.
    await logger.awarning(
        "resolve_league_miss",
        raw_input=raw_input,
        norm=norm,
    )
    await redis.set(miss_key, b"miss", ex=settings.resolve_negative_ttl)
    return None


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
        None — total miss (all stages exhausted + negative cache set),
               or league not in KNOWN_LEAGUES.
        dict — full team row with keys:
            id, league_id, slug, display_name, abbreviation,
            primary_color, secondary_color, logo_url, provider_id,
            logo_variants.

    Both the DB-hit path AND the positive-cache-hit path return this identical
    shape (cache hit re-fetches the full row via ``_fetch_team_by_id``).

    Args:
        league:    League slug (e.g. ``"nba"``), used for league-scope filters
                   and Redis key construction.  Must be in ``KNOWN_LEAGUES``.
        raw_input: Raw user-supplied string (e.g. ``"lakerz"``).
        pool:      Async psycopg3 connection pool.
        redis:     Async Redis client (``decode_responses=False``).

    Returns:
        Full team-row dict or None.
    """
    # Validate league against the fixed six-slug set (WR-03 / T-02-10).
    # Prevents arbitrary strings from polluting Redis key-space and ensures
    # Phase 4 route parameters are never forwarded unchecked.
    if league not in KNOWN_LEAGUES:
        await logger.awarning("resolver_unknown_league", league=league)
        return None

    # T-02-09: reject overlong input before normalization to bound trigram cost.
    # The early return is sufficient — no negative cache write is needed because
    # the overlong branch never reaches the trigram stage anyway.  Writing a
    # miss key with raw (unnormalized) bytes would pollute the keyspace and is
    # never read on subsequent calls (which re-enter this branch immediately).
    if len(raw_input) > _MAX_INPUT_LEN:
        await logger.awarning(
            "resolver_input_too_long",
            league=league,
            length=len(raw_input),
        )
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
    # Stage 3: trigram fuzzy match.
    # (Stage 2 — the casefolded-exact pass — is subsumed by Stage 1:
    # normalize_input already produces norm, so a separate Stage 2 query
    # would issue the same SQL as Stage 1 and waste a DB round-trip.
    # Aliases are always stored fully normalized via generate_aliases(),
    # so no intermediate form can match that Stage 1 would miss.)
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
