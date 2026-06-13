"""Integration tests for Alembic migrations against a live Postgres instance.

These tests require a real Postgres database.  They are skipped automatically
when no database is reachable so the quick-run suite stays green without
external services.

To run with a live DB:
    POSTGRES_DSN=postgresql+psycopg://matchup:matchup@localhost:55432/matchup \\
        uv run pytest tests/test_migrations.py -x -v

CI provides a Postgres service container via .github/workflows/ci.yml; the
POSTGRES_DSN environment variable is set there before pytest runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Generator

import psycopg
import pytest

# ---------------------------------------------------------------------------
# Module-level skip guard
# ---------------------------------------------------------------------------

_POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")

_PG_AVAILABLE: bool = False
if _POSTGRES_DSN:
    try:
        # Attempt a quick synchronous ping to determine availability.
        _ping_dsn = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(_ping_dsn, connect_timeout=3):
            _PG_AVAILABLE = True
    except Exception:
        _PG_AVAILABLE = False

_SKIP_REASON: str = (
    "No live Postgres reachable.  "
    "Set POSTGRES_DSN=postgresql+psycopg://<user>:<pass>@<host>:<port>/<db> to enable."
)

pg_required = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)

# Expected league slugs seeded by the migration
_EXPECTED_LEAGUES: frozenset[str] = frozenset(
    {"nba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"}
)


# ---------------------------------------------------------------------------
# Helper: run alembic as a subprocess
# ---------------------------------------------------------------------------


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Run `uv run alembic <args>` with POSTGRES_DSN in the environment."""
    env = os.environ.copy()
    env["POSTGRES_DSN"] = _POSTGRES_DSN
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _pg_conn() -> psycopg.Connection[psycopg.rows.TupleRow]:
    """Open a synchronous psycopg3 connection to the test database."""
    dsn = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(dsn)


# ---------------------------------------------------------------------------
# Fixtures: run upgrade once per session, downgrade after
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _apply_and_teardown_migration() -> Generator[None]:
    """Apply migrations before the module runs; downgrade to base afterwards."""
    if not _PG_AVAILABLE:
        yield
        return

    # Ensure we start from a clean state
    _run_alembic("downgrade", "base")
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    yield
    _run_alembic("downgrade", "base")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pg_required
def test_alembic_upgrade_head() -> None:
    """REG-04: alembic upgrade head exits 0 against the configured test Postgres."""
    # The module fixture already ran upgrade; just verify the current revision.
    result = _run_alembic("current")
    assert result.returncode == 0, f"alembic current failed:\n{result.stderr}"
    assert "0001" in result.stdout or "0001" in result.stderr, (
        f"Expected revision 0001 to be current.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pg_required
def test_schema_tables_exist() -> None:
    """REG-01: leagues, teams, team_aliases tables exist after upgrade."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('leagues', 'teams', 'team_aliases')
                ORDER BY table_name
                """
        )
        rows = cur.fetchall()

    found = {row[0] for row in rows}
    assert found == {"leagues", "teams", "team_aliases"}, (
        f"Expected tables {{leagues, teams, team_aliases}}, got {found}"
    )


@pg_required
def test_schema_constraints() -> None:
    """REG-02: pg_trgm extension, GIN index, and unique constraint exist."""
    with _pg_conn() as conn, conn.cursor() as cur:
        # pg_trgm extension
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
        ext_row = cur.fetchone()
        assert ext_row is not None, "pg_trgm extension was not created"

        # GIN index on team_aliases.alias
        cur.execute(
            """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'team_aliases'
                  AND indexname = 'ix_team_aliases_alias_trgm'
                """
        )
        idx_row = cur.fetchone()
        assert idx_row is not None, (
            "GIN index ix_team_aliases_alias_trgm does not exist"
        )

        # Unique constraint uq_aliases_league_alias
        cur.execute(
            """
                SELECT conname
                FROM pg_constraint
                WHERE conname = 'uq_aliases_league_alias'
                  AND contype = 'u'
                """
        )
        con_row = cur.fetchone()
        assert con_row is not None, (
            "Unique constraint uq_aliases_league_alias does not exist"
        )


@pg_required
def test_leagues_seeded() -> None:
    """REG-01 / D-04: the six static league rows are present after migration."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM leagues ORDER BY slug")
        rows = cur.fetchall()

    found = {row[0] for row in rows}
    assert found == _EXPECTED_LEAGUES, (
        f"Expected leagues {_EXPECTED_LEAGUES}, got {found}"
    )


@pg_required
def test_espn_id_nullable() -> None:
    """D-02: teams.espn_id is nullable (allows NULL for Phase 2 backfill)."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_name = 'teams'
                  AND column_name = 'espn_id'
                """
        )
        row = cur.fetchone()

    assert row is not None, "teams.espn_id column does not exist"
    assert row[0] == "YES", f"teams.espn_id should be nullable, is_nullable={row[0]}"


@pg_required
def test_unique_constraint_scope_per_league() -> None:
    """D-03: same alias is allowed in two different leagues (per-league uniqueness).

    Inserts a team alias in one league and verifies the same alias can be inserted
    in a different league (cross-league is allowed; within-league is not).
    """
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            # Fetch league IDs for two different leagues
            cur.execute(
                "SELECT id FROM leagues WHERE slug IN ('nba', 'nfl') ORDER BY slug"
            )
            league_rows = cur.fetchall()
            assert len(league_rows) == 2, "Expected NBA and NFL league rows"
            league_id_a, league_id_b = league_rows[0][0], league_rows[1][0]

            # Insert minimal team rows for each league
            cur.execute(
                """
                INSERT INTO teams
                    (league_id, slug, display_name, abbreviation)
                VALUES
                    (%s, 'test-team-a', 'Test Team A', 'TTA'),
                    (%s, 'test-team-b', 'Test Team B', 'TTB')
                RETURNING id
                """,
                (league_id_a, league_id_b),
            )
            team_rows = cur.fetchall()
            team_id_a, team_id_b = team_rows[0][0], team_rows[1][0]

            # Same alias in two different leagues should succeed
            cur.execute(
                """
                INSERT INTO team_aliases (team_id, league_id, alias)
                VALUES
                    (%s, %s, 'bulls'),
                    (%s, %s, 'bulls')
                """,
                (team_id_a, league_id_a, team_id_b, league_id_b),
            )

            # Duplicate alias within the SAME league should fail
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO team_aliases (team_id, league_id, alias)
                    VALUES (%s, %s, 'bulls')
                    """,
                    (team_id_a, league_id_a),
                )
        conn.rollback()  # Clean up test rows
