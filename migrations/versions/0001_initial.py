"""Initial schema: leagues, teams, team_aliases + pg_trgm GIN index + league seed.

Revision ID: 0001
Revises: None
Create Date: 2026-06-13

Notes:
    team_aliases.league_id is a denormalized FK to leagues (not derived from
    team_id -> teams.league_id).  This enables the UNIQUE (league_id, alias)
    constraint without a join.  The invariant team_aliases.league_id ==
    teams.league_id is enforced at the application layer in Phase 2.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial registry schema."""
    # pg_trgm MUST be created before any GIN index that uses gin_trgm_ops
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False),  # e.g. "nba"
        sa.Column("display_name", sa.Text(), nullable=False),  # e.g. "NBA"
        sa.Column("sport", sa.Text(), nullable=False),  # e.g. "basketball"
        sa.UniqueConstraint("slug", name="uq_leagues_slug"),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "league_id",
            sa.Integer(),
            sa.ForeignKey("leagues.id"),
            nullable=False,
        ),
        sa.Column("slug", sa.Text(), nullable=False),  # canonical URL slug
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("abbreviation", sa.Text(), nullable=False),
        sa.Column("primary_color", sa.Text(), nullable=True),  # hex e.g. "#1D428A"
        sa.Column("secondary_color", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),  # ESPN CDN URL
        sa.Column("espn_id", sa.Text(), nullable=True),  # nullable; Phase 2 backfill
        sa.UniqueConstraint("league_id", "slug", name="uq_teams_league_slug"),
    )

    op.create_table(
        "team_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("teams.id"),
            nullable=False,
        ),
        sa.Column(
            "league_id",
            sa.Integer(),
            sa.ForeignKey("leagues.id"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),  # lowercased alias text
        # Per-league uniqueness: same alias allowed across leagues, never within one
        sa.UniqueConstraint("league_id", "alias", name="uq_aliases_league_alias"),
    )

    # GIN chosen over GiST: Phase 2 resolver uses similarity threshold queries
    # (similarity(alias, $q) > 0.5), not ordered distance queries with LIMIT.
    op.create_index(
        "ix_team_aliases_alias_trgm",
        "team_aliases",
        ["alias"],
        postgresql_using="gin",
        postgresql_ops={"alias": "gin_trgm_ops"},
    )

    # Seed the 6 static league rows.  teams/team_aliases populated by Phase 2 seed job.
    op.execute(
        """
        INSERT INTO leagues (slug, display_name, sport) VALUES
        ('nba',   'NBA',              'basketball'),
        ('nfl',   'NFL',              'football'),
        ('mlb',   'MLB',              'baseball'),
        ('nhl',   'NHL',              'hockey'),
        ('ncaaf', 'NCAA Football',    'football'),
        ('ncaab', 'NCAA Basketball',  'basketball')
        """
    )


def downgrade() -> None:
    """Revert the initial registry schema."""
    op.drop_table("team_aliases")
    op.drop_table("teams")
    op.drop_table("leagues")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
