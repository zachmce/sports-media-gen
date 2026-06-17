"""Add logo_url and logo_variants columns to leagues.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-16

Notes:
    Additive migration only — existing leagues rows are preserved unchanged.
    server_default='{}' gives existing rows an empty JSONB object
    immediately so the seed can use `(league["logo_variants"] or {})`
    without a separate None branch.

    Threat mitigations (T-11-03):
    - Pure Alembic op.add_column DDL — no string-interpolated SQL, no user input.
    - Additive columns only; downgrade reverses cleanly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# Alembic revision identifiers
revision: str = "0003"
down_revision: str | None = "0002"  # chain: 0001 → 0002 → 0003 (RESEARCH Pitfall 6)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add logo_url and logo_variants columns to leagues (additive, backward-safe).

    Columns added to the `leagues` table:
    - logo_url:      Text, nullable — the selected ESPN league logo href (LGL-02).
    - logo_variants: JSONB, nullable, server_default '{}' — the full variant map
      ({default: href, dark: href}) as seeded by seed.py (LGL-02, LGL-03).

    No existing leagues rows are modified; no teams data is touched.
    """
    op.add_column(
        "leagues",
        sa.Column("logo_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "leagues",
        sa.Column(
            "logo_variants",
            JSONB,
            nullable=True,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    """Drop the logo_url and logo_variants columns (restores leagues to 0002 shape)."""
    op.drop_column("leagues", "logo_variants")
    op.drop_column("leagues", "logo_url")
