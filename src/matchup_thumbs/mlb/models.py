"""Pydantic v2 models for MLB Stats API responses.

Design decisions mirror espn/models.py:
- ``extra="ignore"`` on every model: MLB payloads carry undocumented fields.
- Required fields (no defaults) on every field the seed depends on.
- Fail loudly on missing required fields; tolerate unknown extras.

Forward-compat trade-off: fail loudly on the fields we read, tolerate fields
we do not.  Full Python 3.14 ``X | None`` union syntax throughout.
"""

from pydantic import BaseModel, ConfigDict, Field


class MLBTeamEntry(BaseModel):
    """A single team object from the MLB Stats API ``teams`` array.

    Required fields match exactly the ProviderTeam fields seed.py writes.
    Optional: shortName (treat as Optional per Pitfall 7 / Assumption A1).
    """

    model_config = ConfigDict(extra="ignore")

    # Required — ValidationError if missing (D-07 fail-loudly)
    id: int  # numeric team ID; provider_id = str(id) (Pitfall 8)
    name: str  # full name e.g. "Toledo Mud Hens" → display_name
    abbreviation: str  # e.g. "TOL" → abbreviation
    teamName: str  # mascot e.g. "Mud Hens" → name, short_display_name (Pitfall 7)
    locationName: str  # city e.g. "Toledo" → location
    active: bool = True

    # Optional — tolerated-but-unused by the provider; do not fail-loudly on
    # drift. clubName is never read by providers/mlb.py (the mapping uses
    # teamName per Pitfall 7), so an upstream rename/removal of this unread
    # field must not abort the whole league seed (WR-01). shortName is
    # unreliable across all teams (Assumption A1).
    clubName: str | None = None
    shortName: str | None = None


class MLBTeamsResponse(BaseModel):
    """Root envelope: ``{"copyright": "...", "teams": [...]}``.

    The envelope is FLAT — ``teams`` is at the root level, unlike ESPN's
    nested ``sports[0].leagues[0].teams`` structure.

    Usage::

        raw = await http_client.get(url)
        response = MLBTeamsResponse.model_validate(raw.json())
        teams = response.teams  # list[MLBTeamEntry]
    """

    model_config = ConfigDict(extra="ignore")

    teams: list[MLBTeamEntry] = Field(..., min_length=0)
