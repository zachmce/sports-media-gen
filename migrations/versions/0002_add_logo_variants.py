"""Add logo_variants JSONB column to teams.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15

Notes:
    Additive migration only — logo_url is preserved unchanged.
    server_default='{}' gives existing rows an empty JSONB object
    immediately so the loader can use `(team["logo_variants"] or {})`
    without a separate None branch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# Alembic revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add logo_variants JSONB column to teams (additive, backward-safe)."""
    op.add_column(
        "teams",
        sa.Column(
            "logo_variants",
            JSONB,
            nullable=True,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    """Drop the logo_variants column (restores teams to revision 0001 shape)."""
    op.drop_column("teams", "logo_variants")
