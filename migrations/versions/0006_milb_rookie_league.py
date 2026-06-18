"""Insert MiLB Rookie league row.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-18

Notes:
    Static INSERT of the milb-rookie league row into ``leagues``.
    Mirrors 0005_milb_leagues.py (which inserted 4 MiLB affiliate rows).

    Pitfall 8 (RESEARCH): down_revision MUST be "0005" — chain is
    0001 → ... → 0005 → 0006.  A wrong down_revision breaks alembic
    downgrade and the migration chain-integrity test.

    team/alias rows populated by seed job, not this migration.
"""

from collections.abc import Sequence

from alembic import op

# Alembic revision identifiers
revision: str = "0006"
down_revision: str | None = "0005"  # Pitfall 8: MUST be "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Insert the milb-rookie league row."""
    op.execute(
        """
        INSERT INTO leagues (slug, display_name, sport) VALUES
        ('milb-rookie', 'MiLB Rookie', 'baseball')
        """
    )


def downgrade() -> None:
    """Remove the milb-rookie league row."""
    op.execute("DELETE FROM leagues WHERE slug = 'milb-rookie'")
