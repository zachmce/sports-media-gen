"""Rename MiLB Single-A slug; insert milb umbrella, milb-winter, milb-independent.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-16

Quick task 260716-ia6: close the delta between our MiLB league set and
``sethwv/game-thumbs``.

Notes:
    down_revision MUST be "0007" — 0007 is the current head.  Chain is
    0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 -> 0008.

    Step 1 is an UPDATE, never DELETE+INSERT: the row's ``id`` is unchanged,
    so existing ``teams.league_id``, ``team_aliases.league_id``, and the
    pre-existing ``singlea`` ``league_aliases`` row all keep pointing at it —
    zero orphans.  This is a deliberate, user-accepted BREAKING rename: no
    compatibility alias is kept for the pre-rename slug, which stops
    resolving entirely (404) after this migration.

    Step 2 (the 3 new rows) hits a trap 0005/0006 did not face: migration
    0007 promoted ``leagues.sport_id`` to NOT NULL, so a bare
    ``INSERT INTO leagues (slug, display_name, sport)`` copied from 0005's
    pattern would raise a NOT NULL violation.  ``sport_id`` is set explicitly
    in the same INSERT via ``(SELECT id FROM sports WHERE slug = 'baseball')``
    — the sports rows are guaranteed to exist because 0007 seeds them and
    runs first in the chain.  The legacy ``sport`` TEXT column is still
    populated too (0007 D-06 retained it, and seed.py's UPDATE derives
    ``sport_id`` from it).

    Runtime consequence of the rename (no code change needed): a stale
    ``leagueresolve:milbsinglea`` Redis entry decodes to a slug no longer in
    ``KNOWN_LEAGUES``, so ``resolve_league``'s existing stale-positive-cache
    guard deletes the key and falls through to a 404 — exactly the intended
    post-rename behaviour.
"""

from collections.abc import Sequence

from alembic import op

# Alembic revision identifiers
revision: str = "0008"
down_revision: str | None = "0007"  # 0007 is the current head
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename the Single-A league row; insert milb / milb-winter / milb-independent."""
    # Step 1: hard rename via UPDATE (never DELETE+INSERT) — FK children survive.
    op.execute(
        """
        UPDATE leagues
        SET slug = 'milb-a', display_name = 'Single-A'
        WHERE slug = 'milb-single-a'
        """
    )

    # Step 2: insert the 3 new rows with sport_id set explicitly via subselect
    # (leagues.sport_id is NOT NULL since 0007 — a bare copy of the 0005/0006
    # INSERT idiom would violate that constraint).
    op.execute(
        """
        INSERT INTO leagues (slug, display_name, sport, sport_id) VALUES
        ('milb', 'Minor League Baseball', 'baseball',
            (SELECT id FROM sports WHERE slug = 'baseball')),
        ('milb-winter', 'Winter Leagues', 'baseball',
            (SELECT id FROM sports WHERE slug = 'baseball')),
        ('milb-independent', 'Independent League Baseball', 'baseball',
            (SELECT id FROM sports WHERE slug = 'baseball'))
        """
    )


def downgrade() -> None:
    """Remove the 3 new rows; restore the Single-A slug."""
    op.execute(
        """
        DELETE FROM leagues
        WHERE slug IN ('milb', 'milb-winter', 'milb-independent')
        """
    )
    op.execute(
        """
        UPDATE leagues
        SET slug = 'milb-single-a', display_name = 'Single-A'
        WHERE slug = 'milb-a'
        """
    )
