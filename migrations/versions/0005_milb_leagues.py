"""Insert 4 MiLB affiliate level league rows.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-18

Notes:
    Static INSERT of the 4 MiLB affiliate level rows into ``leagues``.
    Mirrors the ESPN league seed in migration 0001 lines 89-100.

    Pitfall 2 (RESEARCH): down_revision MUST be "0004" — chain is
    0001 → 0002 → 0003 → 0004 → 0005.  A wrong down_revision breaks
    alembic downgrade and the migration chain-integrity test.

    team/alias rows are populated by the seed job (not this migration).
    This migration only pre-creates the league rows so seed.py's
    ``UPDATE leagues ... WHERE slug=%(slug)s`` finds them (D-15).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0005"
down_revision: str | None = "0004"  # Pitfall 2: MUST be "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Insert the 4 MiLB affiliate level league rows."""
    op.execute(
        """
        INSERT INTO leagues (slug, display_name, sport) VALUES
        ('milb-aaa',      'Triple-A', 'baseball'),
        ('milb-aa',       'Double-A', 'baseball'),
        ('milb-high-a',   'High-A',   'baseball'),
        ('milb-single-a', 'Single-A', 'baseball')
        """
    )


def downgrade() -> None:
    """Remove the 4 MiLB affiliate level league rows."""
    op.execute(
        """
        DELETE FROM leagues
        WHERE slug IN ('milb-aaa', 'milb-aa', 'milb-high-a', 'milb-single-a')
        """
    )
