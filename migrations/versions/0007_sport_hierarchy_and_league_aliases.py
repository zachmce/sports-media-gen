"""Phase 17: sport hierarchy DB foundation.

Creates sports table, adds leagues.sport_id FK, and creates league_aliases.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-19

Scope (D-01: single atomic migration for all of Phase 17):
  1. Create ``sports`` table seeded with 4 canonical rows
     (baseball/basketball/football/hockey).
  2. Add ``leagues.sport_id`` (nullable FK → sports.id), backfill via
     column-to-column join, NULL-guard via explicit count-and-raise
     (SPORT-02), then promote to NOT NULL.
  3. Retain ``leagues.sport`` TEXT column untouched
     (D-06: additive-only, no column drops).
  4. Create empty ``league_aliases`` table with global UNIQUE(alias)
     (D-07) and a pg_trgm GIN index (LALIAS-01).
     Alias seed data is Phase 18 (LALIAS-03).

Pitfall 8 (RESEARCH): down_revision MUST be "0006" — chain is
0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007. A wrong value breaks
alembic downgrade and the migration chain-integrity test.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0007"
down_revision: str | None = "0006"  # Pitfall 8: MUST be "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply Phase 17 schema: sports, leagues.sport_id FK, league_aliases."""
    # Step 1: Create sports table (mirrors leagues naming: id, slug, display_name).
    # slug holds the canonical sport value already used in leagues.sport TEXT.
    op.create_table(
        "sports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False),          # e.g. "baseball"
        sa.Column("display_name", sa.Text(), nullable=False),  # e.g. "Baseball"
        sa.UniqueConstraint("slug", name="uq_sports_slug"),
    )

    # Step 2: Seed 4 canonical sport rows (same static INSERT idiom as 0001/0005/0006).
    # D-03: sports are a fixed canonical set — they belong in the migration so
    # the backfill (Step 4) can reference them in the same transaction.
    op.execute(
        """
        INSERT INTO sports (slug, display_name) VALUES
        ('baseball',   'Baseball'),
        ('basketball', 'Basketball'),
        ('football',   'Football'),
        ('hockey',     'Hockey')
        """
    )

    # Step 3: Add sport_id as NULLABLE first (D-04) — no NOT NULL yet;
    # backfill runs explicitly in Step 4 before constraint is promoted in Step 6.
    op.add_column(
        "leagues",
        sa.Column(
            "sport_id",
            sa.Integer(),
            sa.ForeignKey("sports.id", name="fk_leagues_sport_id"),
            nullable=True,
        ),
    )

    # Step 4: Set-based backfill — all 11 leagues map via their existing
    # leagues.sport TEXT value (D-05). One UPDATE touches all rows in a
    # single round-trip. Column-to-column join: no user input, no injection.
    op.execute(
        """
        UPDATE leagues
        SET sport_id = sports.id
        FROM sports
        WHERE sports.slug = leagues.sport
        """
    )

    # Step 5: NULL-guard (SPORT-02 / D-04) — fail loud before SET NOT NULL
    # so a partial backfill surfaces a clear diagnostic rather than a cryptic
    # Postgres constraint error (Pitfall 3 / RESEARCH).
    # assert satisfies mypy --strict (op.get_bind() returns Connection | None).
    bind = op.get_bind()
    assert bind is not None  # offline mode not used (env.py: online only)
    result = bind.execute(
        sa.text("SELECT count(*) FROM leagues WHERE sport_id IS NULL")
    )
    row = result.fetchone()
    null_count: int = row[0] if row is not None else 0
    if null_count > 0:
        # Fetch offending slugs for a human-readable error message.
        offenders = bind.execute(
            sa.text(
                "SELECT slug FROM leagues WHERE sport_id IS NULL ORDER BY slug"
            )
        )
        bad_slugs = [r[0] for r in offenders.fetchall()]
        raise RuntimeError(
            f"Migration 0007 backfill incomplete: {null_count} league(s) have "
            f"sport_id IS NULL after UPDATE. Unmapped leagues: {bad_slugs}. "
            "Add missing slug→sport mapping in leagues.sport and re-run migration."
        )

    # Step 6: All rows verified non-null — safe to promote column to NOT NULL.
    op.alter_column("leagues", "sport_id", nullable=False)

    # Step 7: Create league_aliases table (LALIAS-01).
    # Mirrors team_aliases in 0001 EXCEPT uniqueness is GLOBAL on alias (D-07):
    # a league alias must resolve to exactly one league (unlike team aliases
    # which allow the same alias across different leagues).
    # Table created empty; Phase 18 (LALIAS-03) populates curated alias rows.
    op.create_table(
        "league_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer(),
            sa.ForeignKey("leagues.id"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        # GLOBAL uniqueness (not per-league) — deliberate divergence from
        # team_aliases which uses UNIQUE(league_id, alias). Rationale: D-07.
        sa.UniqueConstraint("alias", name="uq_league_aliases_alias"),
    )

    # Step 8: pg_trgm GIN index on league_aliases.alias.
    # Mirrors ix_team_aliases_alias_trgm from 0001.
    # Phase 18 resolver will use similarity threshold queries against this index.
    op.create_index(
        "ix_league_aliases_alias_trgm",
        "league_aliases",
        ["alias"],
        postgresql_using="gin",
        postgresql_ops={"alias": "gin_trgm_ops"},
    )


def downgrade() -> None:
    """Reverse Phase 17 schema changes in opposite order to upgrade()."""
    # Drop league_aliases first (no FK dependencies reference it yet).
    op.drop_index("ix_league_aliases_alias_trgm", table_name="league_aliases")
    op.drop_table("league_aliases")

    # Remove sport_id from leagues (FK constraint drops with the column).
    # leagues.sport TEXT is intentionally retained (D-06: additive-only).
    op.drop_column("leagues", "sport_id")

    # Drop sports last — leagues.sport_id referenced it; now that column is gone.
    op.drop_table("sports")
