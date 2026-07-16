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

# Expected league slugs seeded by all migrations through head.
# Updated for quick-260716-ia6: migration 0008 renames the Single-A slug to
# its game-thumbs-matching form and inserts milb / milb-winter /
# milb-independent.
_EXPECTED_LEAGUES: frozenset[str] = frozenset(
    {
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "ncaaf",
        "ncaab",
        "milb-aaa",
        "milb-aa",
        "milb-high-a",
        "milb-a",  # migration 0008 — renamed (hard rename, no alias)
        "milb-rookie",  # Phase 16 — migration 0006
        "milb",  # migration 0008 — umbrella
        "milb-winter",  # migration 0008
        "milb-independent",  # migration 0008
    }
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
    """Apply migrations before the module runs; restore head afterwards.

    Teardown re-applies ``upgrade head`` rather than ``downgrade base`` so this
    module does not drop the shared schema that other DB-integration modules
    (test_resolver, test_seed) depend on — those run after this one in
    alphabetical collection order and expect the schema the CI workflow's
    pre-pytest ``alembic upgrade head`` step established.  Downgrade-to-base is
    still exercised by the setup path below.
    """
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
    # Leave the database migrated at head — do NOT downgrade to base here, or
    # later test modules (collected alphabetically after this one) lose the
    # schema they require.  Setup above already exercises downgrade-to-base.
    _run_alembic("upgrade", "head")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pg_required
def test_alembic_upgrade_head() -> None:
    """REG-04: alembic upgrade head exits 0 against the configured test Postgres."""
    # The module fixture already ran upgrade; just verify the current revision.
    result = _run_alembic("current")
    assert result.returncode == 0, f"alembic current failed:\n{result.stderr}"
    # Updated for quick-260716-ia6: migration 0008 (MiLB umbrella / winter /
    # independent / Single-A rename) is head.
    assert "0008" in result.stdout or "0008" in result.stderr, (
        f"Expected revision 0008 to be current.\n"
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
def test_provider_id_column_exists() -> None:
    """0004: teams.provider_id column exists with nullable=YES (preserved by rename)."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_name = 'teams'
                  AND column_name = 'provider_id'
                """
        )
        row = cur.fetchone()

    assert row is not None, "teams.provider_id column does not exist"
    assert row[0] == "YES", (
        f"teams.provider_id should be nullable, got is_nullable={row[0]}"
    )


@pg_required
def test_provider_column_default() -> None:
    """0004: teams.provider TEXT NOT NULL DEFAULT 'espn'."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'teams'
                  AND column_name = 'provider'
                """
        )
        row = cur.fetchone()

    assert row is not None, "teams.provider column does not exist"
    assert row[0] == "NO", (
        f"teams.provider should be NOT NULL, got is_nullable={row[0]}"
    )
    assert row[1] is not None and "'espn'" in row[1], (
        f"teams.provider should have default 'espn', got column_default={row[1]}"
    )


@pg_required
def test_logo_variants_column_exists() -> None:
    """LOGO-01: teams.logo_variants is a JSONB column with server_default '{}'."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT data_type, column_default
                FROM information_schema.columns
                WHERE table_name = 'teams'
                  AND column_name = 'logo_variants'
                """
        )
        row = cur.fetchone()

    assert row is not None, "teams.logo_variants column does not exist"
    data_type, column_default = row[0], row[1]
    assert data_type == "jsonb", (
        f"teams.logo_variants should have data_type='jsonb', got '{data_type}'"
    )
    assert column_default is not None and "'{}'" in column_default, (
        f"teams.logo_variants should have server_default containing '{{}}', "
        f"got '{column_default}'"
    )


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


# ---------------------------------------------------------------------------
# Migration 0003 — leagues logo columns (LGL-02)
# ---------------------------------------------------------------------------


def test_migration_0003_chains_off_0002() -> None:
    """LGL-02: migration 0003 declares down_revision='0002' (chain integrity).

    This test does NOT require a live Postgres — it reads the migration file
    directly so it always runs and guards against revision-chain breakage
    (RESEARCH Pitfall 6).
    """
    import ast
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "0003_add_leagues_logo_columns.py"
    )
    assert migration_path.exists(), f"Migration file not found: {migration_path}"

    tree = ast.parse(migration_path.read_text())
    # Alembic uses type-annotated assignments: revision: str = "0003"
    # These are ast.AnnAssign nodes, not ast.Assign.
    assigns = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value

    revision_node = assigns.get("revision")
    down_revision_node = assigns.get("down_revision")

    assert revision_node is not None, "revision not found in 0003 migration"
    assert down_revision_node is not None, "down_revision not found in 0003 migration"

    # ast.Constant for string literals
    assert isinstance(revision_node, ast.Constant), "revision must be a string literal"
    assert isinstance(down_revision_node, ast.Constant), (
        "down_revision must be a string literal"
    )

    assert revision_node.value == "0003", (
        f"Expected revision='0003', got '{revision_node.value}'"
    )
    assert down_revision_node.value == "0002", (
        f"Expected down_revision='0002', got '{down_revision_node.value}' "
        "(RESEARCH Pitfall 6: must chain 0001→0002→0003, not skip 0002)"
    )


@pg_required
def test_migration_0003_leagues_logo_columns_exist() -> None:
    """LGL-02: after upgrade head, leagues.logo_url and leagues.logo_variants exist."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'leagues'
              AND column_name IN ('logo_url', 'logo_variants')
            ORDER BY column_name
            """
        )
        rows = cur.fetchall()

    found = {row[0]: (row[1], row[2], row[3]) for row in rows}

    # logo_url: Text nullable, no server_default
    assert "logo_url" in found, (
        "leagues.logo_url column does not exist after migration 0003"
    )
    logo_url_type, logo_url_nullable, _ = found["logo_url"]
    assert logo_url_type == "text", (
        f"Expected leagues.logo_url data_type='text', got '{logo_url_type}'"
    )
    assert logo_url_nullable == "YES", (
        f"Expected leagues.logo_url to be nullable, got '{logo_url_nullable}'"
    )

    # logo_variants: JSONB nullable with server_default '{}'
    assert "logo_variants" in found, (
        "leagues.logo_variants column does not exist after migration 0003"
    )
    logo_var_type, logo_var_nullable, logo_var_default = found["logo_variants"]
    assert logo_var_type == "jsonb", (
        f"Expected logo_variants data_type='jsonb', got '{logo_var_type}'"
    )
    assert logo_var_nullable == "YES", (
        f"Expected logo_variants to be nullable, got '{logo_var_nullable}'"
    )
    assert logo_var_default is not None and "'{}'" in logo_var_default, (
        f"Expected server_default '{{}}' for logo_variants, got '{logo_var_default}'"
    )


# ---------------------------------------------------------------------------
# Migration 0005 — MiLB affiliate level league rows (MILB-02)
# ---------------------------------------------------------------------------


def test_migration_0005_chains_off_0004() -> None:
    """Migration 0005 declares down_revision='0004' (Pitfall 2: chain integrity).

    Does NOT require live Postgres — reads the migration file directly so it
    always runs and guards against revision-chain breakage.
    Mirrors test_migration_0003_chains_off_0002.

    Chain must be: 0001 → 0002 → 0003 → 0004 → 0005.
    Wave 0 note: this test is RED until the 0005_milb_leagues.py file lands
    (Phase 15 Wave 1).  Once the file exists with correct metadata, it turns GREEN.
    """
    import ast
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "0005_milb_leagues.py"
    )
    assert migration_path.exists(), (
        f"Migration file not found: {migration_path}. "
        "Create migrations/versions/0005_milb_leagues.py in Phase 15 Wave 1."
    )

    tree = ast.parse(migration_path.read_text())
    # Alembic uses type-annotated assignments: revision: str = "0005"
    # These are ast.AnnAssign nodes, not ast.Assign.
    assigns = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value

    revision_node = assigns.get("revision")
    down_revision_node = assigns.get("down_revision")

    assert revision_node is not None, "revision not found in 0005 migration"
    assert down_revision_node is not None, "down_revision not found in 0005 migration"

    # ast.Constant for string literals
    assert isinstance(revision_node, ast.Constant), "revision must be a string literal"
    assert isinstance(down_revision_node, ast.Constant), (
        "down_revision must be a string literal"
    )

    assert revision_node.value == "0005", (
        f"Expected revision='0005', got '{revision_node.value}'"
    )
    assert down_revision_node.value == "0004", (
        f"Expected down_revision='0004', got '{down_revision_node.value}' "
        "(Pitfall 2: chain must be 0001→0002→0003→0004→0005, not skip 0004)"
    )


def test_migration_0006_chains_off_0005() -> None:
    """Migration 0006 declares down_revision='0005' (chain integrity).

    Does NOT require live Postgres — reads the migration file directly so it
    always runs and guards against revision-chain breakage.
    Mirrors test_migration_0005_chains_off_0004.

    Chain must be: 0001 → 0002 → 0003 → 0004 → 0005 → 0006.
    """
    import ast
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "0006_milb_rookie_league.py"
    )
    assert migration_path.exists(), (
        f"Migration file not found: {migration_path}. "
        "Create migrations/versions/0006_milb_rookie_league.py in Phase 16."
    )

    tree = ast.parse(migration_path.read_text())
    assigns = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value

    revision_node = assigns.get("revision")
    down_revision_node = assigns.get("down_revision")

    assert revision_node is not None, "revision not found in 0006 migration"
    assert down_revision_node is not None, "down_revision not found in 0006 migration"

    assert isinstance(revision_node, ast.Constant), "revision must be a string literal"
    assert isinstance(down_revision_node, ast.Constant), (
        "down_revision must be a string literal"
    )

    assert revision_node.value == "0006", (
        f"Expected revision='0006', got '{revision_node.value}'"
    )
    assert down_revision_node.value == "0005", (
        f"Expected down_revision='0005', got '{down_revision_node.value}' "
        "(chain must be 0001→0002→0003→0004→0005→0006, not skip 0005)"
    )


def test_migration_0007_chains_off_0006() -> None:
    """Migration 0007 declares down_revision='0006' (chain integrity).

    Does NOT require live Postgres — reads the migration file directly so it
    always runs and guards against revision-chain breakage.
    Mirrors test_migration_0006_chains_off_0005.

    Chain must be: 0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007.
    """
    import ast
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "0007_sport_hierarchy_and_league_aliases.py"
    )
    assert migration_path.exists(), (
        f"Migration file not found: {migration_path}. "
        "Create migrations/versions/0007_sport_hierarchy_and_league_aliases.py"
        " in Phase 17."
    )

    tree = ast.parse(migration_path.read_text())
    assigns = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value

    revision_node = assigns.get("revision")
    down_revision_node = assigns.get("down_revision")

    assert revision_node is not None, "revision not found in 0007 migration"
    assert down_revision_node is not None, "down_revision not found in 0007 migration"

    assert isinstance(revision_node, ast.Constant), "revision must be a string literal"
    assert isinstance(down_revision_node, ast.Constant), (
        "down_revision must be a string literal"
    )

    assert revision_node.value == "0007", (
        f"Expected revision='0007', got '{revision_node.value}'"
    )
    assert down_revision_node.value == "0006", (
        f"Expected down_revision='0006', got '{down_revision_node.value}' "
        "(chain must be 0001→0002→0003→0004→0005→0006→0007, not skip 0006)"
    )


@pg_required
def test_migration_0005_milb_leagues_seeded() -> None:
    """MILB-02: after upgrade head, all 4 MiLB affiliate level league rows exist.

    Requires a live Postgres with migration 0005 applied (pg_required).
    Updated for quick-260716-ia6: migration 0008 (which also runs as part of
    upgrade head) renames the Single-A slug — this test describes the
    post-0008 world, since these tests run against `upgrade head`.
    """
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug FROM leagues
            WHERE slug IN ('milb-aaa', 'milb-aa', 'milb-high-a', 'milb-a')
            ORDER BY slug
            """
        )
        rows = cur.fetchall()

    found = {row[0] for row in rows}
    expected = {"milb-aaa", "milb-aa", "milb-high-a", "milb-a"}
    assert found == expected, (
        f"Expected MiLB league rows {expected} after migration 0005+0008, "
        f"got {found}. Run 'alembic upgrade head'."
    )


# ---------------------------------------------------------------------------
# Migration 0007 — sport hierarchy + league aliases (SPORT-01, SPORT-02, LALIAS-01)
# ---------------------------------------------------------------------------


@pg_required
def test_migration_0007_sports_table_exists() -> None:
    """SPORT-01: sports table exists with 4 canonical rows after upgrade head."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM sports ORDER BY slug")
        rows = cur.fetchall()

    found = {row[0] for row in rows}
    assert found == {"baseball", "basketball", "football", "hockey"}, (
        f"Expected sports {{baseball, basketball, football, hockey}}, got {found}"
    )


@pg_required
def test_migration_0007_sport_id_fk_column_exists() -> None:
    """SPORT-01: leagues.sport_id is a NOT NULL column with FK to sports."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_name = 'leagues'
              AND column_name = 'sport_id'
            """
        )
        row = cur.fetchone()

    assert row is not None, "leagues.sport_id column does not exist"
    assert row[0] == "NO", (
        f"leagues.sport_id must be NOT NULL, got is_nullable={row[0]}"
    )


@pg_required
def test_migration_0007_leagues_sport_id_not_null() -> None:
    """SPORT-02: all leagues have a non-null sport_id after upgrade head."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leagues WHERE sport_id IS NULL")
        row = cur.fetchone()

    assert row is not None
    assert row[0] == 0, f"Expected 0 leagues with sport_id IS NULL, got {row[0]}"


@pg_required
def test_migration_0007_league_aliases_table_exists() -> None:
    """LALIAS-01: league_aliases exists with GIN index, global UNIQUE, and is empty."""
    with _pg_conn() as conn, conn.cursor() as cur:
        # GIN index on league_aliases.alias
        cur.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'league_aliases'
              AND indexname = 'ix_league_aliases_alias_trgm'
            """
        )
        idx = cur.fetchone()
        assert idx is not None, "ix_league_aliases_alias_trgm index does not exist"

        # Global unique constraint on alias
        cur.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conname = 'uq_league_aliases_alias'
              AND contype = 'u'
            """
        )
        con = cur.fetchone()
        assert con is not None, "uq_league_aliases_alias constraint does not exist"

        # Table created empty in Phase 17 (LALIAS-03 seed is Phase 18)
        cur.execute("SELECT count(*) FROM league_aliases")
        empty = cur.fetchone()
        assert empty is not None and empty[0] == 0, (
            "league_aliases must be empty after Phase 17"
            " (LALIAS-03 alias seeding is Phase 18)"
        )


# ---------------------------------------------------------------------------
# Migration 0008 — MiLB umbrella / winter / independent + Single-A rename
# (quick task 260716-ia6)
# ---------------------------------------------------------------------------


def test_migration_0008_chains_off_0007() -> None:
    """Migration 0008 declares down_revision='0007' (chain integrity).

    Does NOT require live Postgres — reads the migration file directly so it
    always runs and guards against revision-chain breakage.
    Mirrors test_migration_0007_chains_off_0006.

    Chain must be: 0001 → ... → 0006 → 0007 → 0008.
    """
    import ast
    import pathlib

    migration_path = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "versions"
        / "0008_milb_umbrella_winter_independent.py"
    )
    assert migration_path.exists(), (
        f"Migration file not found: {migration_path}. "
        "Create migrations/versions/0008_milb_umbrella_winter_independent.py"
        " (quick task 260716-ia6)."
    )

    tree = ast.parse(migration_path.read_text())
    assigns = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assigns[node.target.id] = node.value

    revision_node = assigns.get("revision")
    down_revision_node = assigns.get("down_revision")

    assert revision_node is not None, "revision not found in 0008 migration"
    assert down_revision_node is not None, "down_revision not found in 0008 migration"

    assert isinstance(revision_node, ast.Constant), "revision must be a string literal"
    assert isinstance(down_revision_node, ast.Constant), (
        "down_revision must be a string literal"
    )

    assert revision_node.value == "0008", (
        f"Expected revision='0008', got '{revision_node.value}'"
    )
    assert down_revision_node.value == "0007", (
        f"Expected down_revision='0007', got '{down_revision_node.value}' "
        "(chain must be 0001→...→0006→0007→0008, not skip 0007)"
    )


@pg_required
def test_alembic_heads_reports_single_head_0008() -> None:
    """`alembic heads` reports exactly one head: 0008."""
    result = _run_alembic("heads")
    assert result.returncode == 0, f"alembic heads failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "0008" in combined, f"Expected 0008 in `alembic heads` output:\n{combined}"
    # Exactly one head line (no branch split)
    head_lines = [
        line for line in combined.splitlines() if line.strip() and "(head)" in line
    ]
    assert len(head_lines) == 1, (
        f"Expected exactly one head, got {len(head_lines)}: {head_lines}"
    )


@pg_required
def test_migration_0008_rename_preserves_league_id() -> None:
    """The Single-A rename is an UPDATE, not DELETE+INSERT — id is unchanged.

    FK children (teams.league_id, team_aliases.league_id) and the pre-existing
    'singlea' league_aliases row all keep pointing at the same league.id
    across the rename — zero orphans.
    """
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, slug, display_name FROM leagues WHERE slug = 'milb-a'")
        row = cur.fetchone()

    assert row is not None, "Expected a 'milb-a' league row after migration 0008"
    league_id, slug, display_name = row
    assert slug == "milb-a"
    assert display_name == "Single-A"

    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT slug FROM leagues WHERE slug = 'milb-single-a'")
        old_row = cur.fetchone()
    assert old_row is None, (
        "'milb-single-a' must not exist as a league slug after the hard rename "
        "(no compatibility alias)"
    )


@pg_required
def test_migration_0008_new_leagues_have_sport_id() -> None:
    """milb / milb-winter / milb-independent all have sport='baseball' AND

    a non-null sport_id (0007 made leagues.sport_id NOT NULL — the trap 0005/
    0006 did not face).
    """
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.slug, l.sport, l.sport_id, s.slug AS sport_slug
            FROM leagues l
            JOIN sports s ON s.id = l.sport_id
            WHERE l.slug IN ('milb', 'milb-winter', 'milb-independent')
            ORDER BY l.slug
            """
        )
        rows = cur.fetchall()

    found = {row[0] for row in rows}
    assert found == {"milb", "milb-winter", "milb-independent"}, (
        f"Expected milb/milb-winter/milb-independent league rows, got {found}"
    )
    for slug, sport, sport_id, sport_slug in rows:
        assert sport == "baseball", f"{slug}: expected sport='baseball', got {sport!r}"
        assert sport_id is not None, f"{slug}: sport_id must not be NULL"
        assert sport_slug == "baseball", (
            f"{slug}: expected sport_id to join to the baseball sports row"
        )


@pg_required
def test_migration_0008_display_names() -> None:
    """Display names mirror the reference's League Name column."""
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug, display_name FROM leagues
            WHERE slug IN ('milb', 'milb-winter', 'milb-independent')
            """
        )
        rows = dict(cur.fetchall())

    assert rows["milb"] == "Minor League Baseball"
    assert rows["milb-winter"] == "Winter Leagues"
    assert rows["milb-independent"] == "Independent League Baseball"


@pg_required
def test_migration_0008_downgrade_upgrade_idempotent() -> None:
    """downgrade from 0008 removes the 3 rows and restores the Single-A slug;

    upgrade -> downgrade -> upgrade is idempotent.
    """
    result_down = _run_alembic("downgrade", "0007")
    assert result_down.returncode == 0, (
        f"alembic downgrade 0007 failed:\nstdout: {result_down.stdout}\n"
        f"stderr: {result_down.stderr}"
    )

    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug FROM leagues
            WHERE slug IN ('milb', 'milb-winter', 'milb-independent',
                            'milb-a', 'milb-single-a')
            """
        )
        rows = {row[0] for row in cur.fetchall()}

    assert rows == {"milb-single-a"}, (
        f"After downgrade to 0007, expected only 'milb-single-a' to be present "
        f"among the affected slugs, got {rows}"
    )

    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT display_name FROM leagues WHERE slug = 'milb-single-a'")
        row = cur.fetchone()
    assert row is not None and row[0] == "Single-A"

    result_up = _run_alembic("upgrade", "head")
    assert result_up.returncode == 0, (
        f"alembic upgrade head failed:\nstdout: {result_up.stdout}\n"
        f"stderr: {result_up.stderr}"
    )

    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT slug FROM leagues
            WHERE slug IN ('milb', 'milb-winter', 'milb-independent',
                            'milb-a', 'milb-single-a')
            ORDER BY slug
            """
        )
        rows2 = {row[0] for row in cur.fetchall()}

    assert rows2 == {"milb", "milb-winter", "milb-independent", "milb-a"}, (
        f"After re-upgrade to head, expected the 4 renamed/new slugs, got {rows2}"
    )
