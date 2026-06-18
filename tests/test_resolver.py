"""Resolver tests covering RES-01 through RES-06.

Integration tests (test_resolver_exact, test_resolver_normalized,
test_resolver_fuzzy, test_resolver_scope, test_resolver_404,
test_resolver_cache) require a live Postgres instance with the
seeded_registry fixture.  They are skipped automatically when POSTGRES_DSN is
not set or unreachable.

The cache-behaviour test (test_resolver_cache) uses a live Postgres pool for
the _fetch_team_by_id re-fetch path, proving the WARNING 5 contract holds:
a cache-hit returns the same full-row dict shape as a DB hit.

Unit tests (test_resolver_overlong_input, test_resolver_negative_cache_short_circuits)
use mock_pool/mock_redis and do not require live services.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg_pool import AsyncConnectionPool

from matchup_thumbs.resolver import resolve
from matchup_thumbs.settings import settings
from tests.conftest import pg_required

# ---------------------------------------------------------------------------
# Live pool fixture (guarded by pg_required)
# ---------------------------------------------------------------------------

_POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")


@pytest.fixture
async def live_pool() -> AsyncIterator[AsyncConnectionPool]:
    """Async psycopg3 connection pool pointing at the live test Postgres DB.

    Tests that use this fixture must also use the pg_required mark so the
    fixture is skipped when Postgres is unavailable.
    """
    conninfo = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")
    async with AsyncConnectionPool(conninfo=conninfo, min_size=1, max_size=2) as pool:
        yield pool


# ---------------------------------------------------------------------------
# RES-01: Exact alias match
# ---------------------------------------------------------------------------


@pg_required
async def test_resolver_exact(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-01: 'lakers' resolves to the Lakers via Stage 1 exact alias match."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    row = await resolve("nba", "lakers", live_pool, redis)

    assert row is not None, "Expected a match for 'lakers' in NBA"
    assert row["slug"] == "los-angeles-lakers"
    assert row["display_name"] == "Los Angeles Lakers"
    _assert_full_row_shape(row)


# ---------------------------------------------------------------------------
# RES-02 / RES-03: Normalised + fuzzy resolution
# ---------------------------------------------------------------------------


@pg_required
async def test_resolver_normalized(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-02/03: 'LA-Lakers' normalises to 'lalakers', Stage 3 trigram (~0.778)."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    row = await resolve("nba", "LA-Lakers", live_pool, redis)

    assert row is not None, "Expected a match for 'LA-Lakers' in NBA"
    assert row["slug"] == "los-angeles-lakers"
    _assert_full_row_shape(row)


@pg_required
async def test_resolver_fuzzy(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-03: 'lakerz' (typo) resolves to Lakers via Stage 3 trigram (~0.556 > 0.5)."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    row = await resolve("nba", "lakerz", live_pool, redis)

    assert row is not None, "Expected a match for 'lakerz' in NBA"
    assert row["slug"] == "los-angeles-lakers"
    _assert_full_row_shape(row)


# ---------------------------------------------------------------------------
# RES-04: League-scope isolation
# ---------------------------------------------------------------------------


@pg_required
async def test_resolver_scope(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-04: 'lac' resolves to different teams in NBA vs NFL (league-scoped)."""
    redis_nba = MagicMock()
    redis_nba.get = AsyncMock(return_value=None)
    redis_nba.set = AsyncMock()
    redis_nba.delete = AsyncMock()

    redis_nfl = MagicMock()
    redis_nfl.get = AsyncMock(return_value=None)
    redis_nfl.set = AsyncMock()
    redis_nfl.delete = AsyncMock()

    nba_row = await resolve("nba", "lac", live_pool, redis_nba)
    nfl_row = await resolve("nfl", "lac", live_pool, redis_nfl)

    assert nba_row is not None, "Expected 'lac' to resolve in NBA"
    assert nfl_row is not None, "Expected 'lac' to resolve in NFL"

    # Critical RES-04 assertion: same alias, different leagues, different teams.
    assert nba_row["slug"] == "los-angeles-clippers", (
        f"NBA 'lac' should be Clippers, got {nba_row['slug']!r}"
    )
    assert nfl_row["slug"] == "los-angeles-chargers", (
        f"NFL 'lac' should be Chargers, got {nfl_row['slug']!r}"
    )

    # Explicit cross-league safety check (T-02-11)
    assert nba_row["slug"] != "los-angeles-chargers", (
        "NBA 'lac' must never resolve to the Chargers"
    )
    assert nfl_row["slug"] != "los-angeles-clippers", (
        "NFL 'lac' must never resolve to the Clippers"
    )

    _assert_full_row_shape(nba_row)
    _assert_full_row_shape(nfl_row)


# ---------------------------------------------------------------------------
# RES-06: Unresolvable input → None + negative cache set
# ---------------------------------------------------------------------------


@pg_required
async def test_resolver_404(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-06: 'zzzznotateam' returns None and sets the negative cache key."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    result = await resolve("nba", "zzzznotateam", live_pool, redis)

    assert result is None, "Expected None for an unresolvable input"

    # Negative cache key must be set with the 5-min TTL (RES-06 / D-14)
    redis.set.assert_called_once()
    call_args = redis.set.call_args
    key_bytes: bytes = call_args[0][0]
    value_bytes: bytes = call_args[0][1]
    assert key_bytes == b"resolve_miss:nba:zzzznotateam", (
        f"Expected negative cache key, got {key_bytes!r}"
    )
    assert value_bytes == b"miss"
    assert call_args[1].get("ex") == settings.resolve_negative_ttl


# ---------------------------------------------------------------------------
# RES-05 + WARNING 5 contract: cache-hit path returns full row shape
# ---------------------------------------------------------------------------


@pg_required
async def test_resolver_cache(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """RES-05 + WARNING 5: positive cache hit re-fetches the full team row.

    First call hits DB, caches the team_id with 7-day TTL.
    Second call (cache populated) must return the SAME dict shape — same keys,
    same slug — proving _fetch_team_by_id is used rather than returning {id}.
    """
    # ---- First call: DB hit ----
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    first = await resolve("nba", "lakers", live_pool, redis)
    assert first is not None

    # Verify the positive cache write used the 7-day TTL
    redis.set.assert_called_once()
    set_call = redis.set.call_args
    cache_key: bytes = set_call[0][0]
    cached_id_bytes: bytes = set_call[0][1]
    assert cache_key == b"resolve:nba:lakers"
    assert set_call[1].get("ex") == settings.resolve_positive_ttl  # 604800

    # ---- Second call: cache hit ----
    # Simulate positive cache returning the team_id bytes.
    # redis.get is called twice: once for cache_key, once for miss_key.
    # With a cache hit, first get returns cached_id_bytes and the function
    # returns early without checking miss_key.
    redis2 = MagicMock()
    redis2.get = AsyncMock(return_value=cached_id_bytes)
    redis2.set = AsyncMock()
    redis2.delete = AsyncMock()

    second = await resolve("nba", "lakers", live_pool, redis2)
    assert second is not None

    # WARNING 5 contract: cache-hit dict shape == DB-hit dict shape
    assert set(second.keys()) == set(first.keys()), (
        "Cache-hit and DB-hit must return identical dict keys"
    )
    assert second["slug"] == first["slug"], (
        "Cache-hit must return the same team slug as DB-hit"
    )
    assert second["display_name"] == first["display_name"]
    _assert_full_row_shape(second)


# ---------------------------------------------------------------------------
# Helper: assert the locked return-contract shape (WARNING 5)
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = frozenset(
    {
        "id",
        "league_id",
        "slug",
        "display_name",
        "abbreviation",
        "primary_color",
        "secondary_color",
        "logo_url",
        "provider_id",
        "logo_variants",
    }
)


def _assert_full_row_shape(row: dict) -> None:  # type: ignore[type-arg]
    """Assert the team row dict has exactly the locked contract keys.

    Verifies WARNING 5 / RES return contract: no more, no fewer keys,
    regardless of whether the row came from a DB hit or a cache hit.
    The ``sim`` column (trigram similarity) must NOT leak into the result.
    """
    actual_keys = frozenset(row.keys())
    # sim column must not leak from the trigram stage
    assert "sim" not in actual_keys, (
        "trigram 'sim' column must not appear in result dict"
    )
    # All contract keys must be present
    missing = _EXPECTED_KEYS - actual_keys
    assert not missing, f"Missing keys in team row: {missing}"
    # No extra keys beyond the contract
    extra = actual_keys - _EXPECTED_KEYS
    assert not extra, f"Extra unexpected keys in team row: {extra}"


# ---------------------------------------------------------------------------
# Unit test: overlong input guard (T-02-09) — no Postgres needed
# ---------------------------------------------------------------------------


async def test_resolver_overlong_input(
    mock_pool: MagicMock,
    mock_redis: MagicMock,
) -> None:
    """T-02-09: Input exceeding 100 chars returns None without hitting Postgres."""
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    long_input = "a" * 101
    result = await resolve("nba", long_input, mock_pool, mock_redis)

    assert result is None
    # Pool should NOT be touched (no DB query for overlong input)
    mock_pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# Unit test: negative cache short-circuit — no Postgres needed
# ---------------------------------------------------------------------------


async def test_resolver_negative_cache_short_circuits(
    mock_pool: MagicMock,
    mock_redis: MagicMock,
) -> None:
    """Negative cache hit short-circuits all DB queries."""
    # Simulate: positive cache miss, then negative cache hit
    mock_redis.get = AsyncMock(side_effect=[None, b"miss"])
    mock_redis.set = AsyncMock()

    result = await resolve("nba", "zzzznotateam", mock_pool, mock_redis)

    assert result is None
    # Pool.connection must NOT have been called (trigram scan short-circuited)
    mock_pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 15 Wave 0: MiLB resolver scaffolds (pg_required — skip without Postgres)
# ---------------------------------------------------------------------------


@pg_required
async def test_milb_resolver_cross_level_isolation(
    seeded_registry: None,
    live_pool: AsyncConnectionPool,
) -> None:
    """MILB-03 / D-09: Cross-level abbreviation collision handled by league_id scoping.

    Seeds a 'COL' alias under milb-aaa (Columbus Clippers) and verifies that
    the same alias does NOT resolve under milb-aa (D-09 — resolver is already
    league-scoped; no new mechanism needed).

    This test requires:
    1. Migration 0005 applied (milb-aaa and milb-aa league rows exist).
    2. A milb-aaa team seeded with alias 'col'.

    Until both conditions are met (Wave 2 seed + migration 0005), this test
    will skip via pg_required when Postgres is absent, or will fail gracefully
    when the milb-aaa league row doesn't exist yet (Wave 0 is test scaffolding).
    """
    import psycopg

    raw_dsn = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")

    # Check milb-aaa and milb-aa league rows exist (only after migration 0005)
    with psycopg.connect(raw_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT slug, id FROM leagues WHERE slug IN ('milb-aaa', 'milb-aa')"
            )
            league_rows = {row[0]: row[1] for row in cur.fetchall()}

    if "milb-aaa" not in league_rows or "milb-aa" not in league_rows:
        pytest.skip(
            "milb-aaa and milb-aa league rows not yet seeded (migration 0005 "
            "not applied). Will run after Wave 1 migration lands."
        )

    aaa_id = league_rows["milb-aaa"]
    aa_id = league_rows["milb-aa"]

    with psycopg.connect(raw_dsn) as conn:
        with conn.cursor() as cur:
            # Seed minimal Columbus Clippers row under milb-aaa
            cur.execute(
                """
                INSERT INTO teams
                    (league_id, slug, display_name, abbreviation)
                VALUES (%s, 'columbus-clippers', 'Columbus Clippers', 'COL')
                ON CONFLICT (league_id, slug) DO NOTHING
                RETURNING id
                """,
                (aaa_id,),
            )
            row = cur.fetchone()
            if row is None:
                # Already exists — fetch the id
                cur.execute(
                    "SELECT id FROM teams WHERE league_id = %s AND slug = 'columbus-clippers'",
                    (aaa_id,),
                )
                fetched = cur.fetchone()
                assert fetched is not None
                clippers_aaa_id = fetched[0]
            else:
                clippers_aaa_id = row[0]

            # Upsert 'col' alias for milb-aaa Columbus Clippers
            cur.execute(
                """
                INSERT INTO team_aliases (team_id, league_id, alias)
                VALUES (%s, %s, 'col')
                ON CONFLICT (league_id, alias) DO NOTHING
                """,
                (clippers_aaa_id, aaa_id),
            )
        conn.commit()

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()

    # 'col' resolves under milb-aaa (Columbus Clippers is there)
    aaa_result = await resolve("milb-aaa", "col", live_pool, redis)
    assert aaa_result is not None, (
        "Expected 'col' to resolve under milb-aaa (Columbus Clippers)"
    )
    assert aaa_result["slug"] == "columbus-clippers"

    # 'col' does NOT resolve under milb-aa (no team seeded there with that alias)
    redis.get = AsyncMock(return_value=None)  # reset cache mock
    aa_result = await resolve("milb-aa", "col", live_pool, redis)
    assert aa_result is None, (
        f"Expected 'col' to NOT resolve under milb-aa (cross-level isolation). "
        f"Got: {aa_result!r}"
    )

    # Cleanup
    with psycopg.connect(raw_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM team_aliases WHERE team_id = %s AND league_id = %s",
                (clippers_aaa_id, aaa_id),
            )
            cur.execute(
                "DELETE FROM teams WHERE id = %s",
                (clippers_aaa_id,),
            )
        conn.commit()
