"""Rename teams.espn_id → provider_id and add provider discriminator column.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-17

Notes:
    Two additive-style changes:

    1. Rename: ``teams.espn_id`` → ``teams.provider_id``
       Pure column rename — no data migration.  The opaque ESPN team ID value
       (e.g. ``"13"`` for the Lakers) is preserved byte-for-byte in the new
       column (D-07).  ``espn_id`` was ``nullable=True`` in 0001; the rename
       preserves that nullability (Alembic op.alter_column with only
       new_column_name does not touch nullable/NOT NULL per Pitfall 8).

    2. Add: ``teams.provider TEXT NOT NULL server_default='espn'``
       Discriminator column identifying the data provider.  ``server_default``
       backfills all existing rows at DDL time so the NOT NULL constraint is
       satisfied immediately without a two-step migration (Postgres fills
       existing rows with the default when both NOT NULL and server_default
       are specified).

    No index exists on ``espn_id`` (confirmed by audit: 0001 defines the
    column with no explicit index) so no index drop or recreate is needed.

    Threat mitigations (T-14-04):
    - Pure Alembic op.alter_column + op.add_column DDL — no string-interpolated
      SQL, no user input.
    - downgrade reverses in the correct order: drop provider first, then rename
      provider_id back to espn_id.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename espn_id → provider_id and add provider discriminator column."""
    # Step 1: rename espn_id → provider_id (preserves nullable=True per Pitfall 8)
    op.alter_column("teams", "espn_id", new_column_name="provider_id")
    # Step 2: add provider discriminator; server_default backfills existing rows
    op.add_column(
        "teams",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'espn'"),
        ),
    )


def downgrade() -> None:
    """Drop provider column and rename provider_id back to espn_id."""
    # Reverse in the opposite order to upgrade
    op.drop_column("teams", "provider")
    op.alter_column("teams", "provider_id", new_column_name="espn_id")
