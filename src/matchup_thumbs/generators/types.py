"""Shared TypedDict contracts for generator functions and the asset loader.

These types define the input/output contracts for the pure generator pipeline:
- TeamDict: resolved team row from resolver.py (matches _TEAM_COLUMNS)
- DecodedAssets: decoded PIL.Image logos from the asset loader

Both TypedDicts flow into generator functions which are pure
(TeamDict, TeamDict, DecodedAssets) → PIL.Image with no I/O (GEN-04).
"""

from __future__ import annotations

from typing import TypedDict

from PIL import Image


class TeamDict(TypedDict):
    """Resolved team record as returned by resolver.resolve_team().

    Field order and types match ``resolver._TEAM_COLUMNS``:
    ``t.id, t.league_id, t.slug, t.display_name, t.abbreviation,
    t.primary_color, t.secondary_color, t.logo_url, t.espn_id``

    Colors are ``#RRGGBB`` hex strings or ``None`` for unset teams (D-15).
    ``espn_id`` is Text (treated as opaque string per DB schema decision).
    """

    id: int
    league_id: int
    slug: str
    display_name: str
    abbreviation: str
    primary_color: str | None
    secondary_color: str | None
    logo_url: str | None
    espn_id: str


class DecodedAssets(TypedDict):
    """Pre-decoded PIL.Image logos for both matchup teams.

    Produced by the asset loader (assets/loader.py) which is the only I/O
    component in the render pipeline.  Generators receive these already-decoded
    images and perform no further I/O (GEN-04).

    Both images are RGBA mode (loader calls ``.convert("RGBA")``).
    """

    away_logo: Image.Image
    home_logo: Image.Image
