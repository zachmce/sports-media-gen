"""Pydantic v2 models for ESPN team API responses.

These models cover the ``/apis/site/v2/sports/{sport}/{league}/teams``
endpoint envelope.  Design decisions (ESPN-03 / D-07):

- ``extra="ignore"`` on every model: ESPN payloads carry many undocumented
  sibling fields.  Forbidding extras would make the seed brittle against
  benign additive ESPN changes (false-positive drift) with no safety benefit,
  because ignoring an unknown EXTRA field never masks a MISSING required one.

- Required fields (no defaults) on every field the seed depends on: a missing
  or renamed depended-on field (the realistic drift failure mode) raises
  ``pydantic.ValidationError`` at parse time and fails the seed loudly.

Forward-compat trade-off: fail loudly on the fields we read, tolerate fields
we do not.  Full Python 3.14 ``X | None`` union syntax throughout.
"""

from pydantic import BaseModel, ConfigDict


class ESPNLogo(BaseModel):
    """A single logo entry from the ESPN ``logos`` array."""

    model_config = ConfigDict(extra="ignore")

    href: str  # ESPN CDN URL; use directly — never reconstruct (D-10)
    rel: list[str]  # e.g. ["full", "default"], ["full", "dark"]


class ESPNTeamEntry(BaseModel):
    """The inner ``team`` object from ESPN.

    Required fields (no defaults) are those the seed reads and maps to the
    database schema.  Optional fields use sensible defaults so seed logic
    can reference them without defensive None-checking on every access.
    """

    model_config = ConfigDict(extra="ignore")

    # Required — seed depends on all of these; missing any raises ValidationError
    id: str  # ESPN team ID, opaque string (Text column per Phase 1 decision)
    slug: str  # e.g. "los-angeles-lakers"; upsert key with league_id
    abbreviation: str  # e.g. "LAL"; alias source
    displayName: str  # e.g. "Los Angeles Lakers"; alias source
    shortDisplayName: str  # e.g. "Lakers"; alias source
    name: str  # mascot name; e.g. "Lakers"; alias source
    location: str  # city/region; e.g. "Los Angeles"; alias source

    # Optional — normalize at seed time: prepend '#' if present
    color: str | None = None  # primary hex WITHOUT '#'; e.g. "552583"
    alternateColor: str | None = None  # secondary hex WITHOUT '#'

    # 79 NCAAF teams return no logos array → placeholder fallback (ESPN-02)
    logos: list[ESPNLogo] = []

    isActive: bool = True  # filter to active teams; all current teams = True


class ESPNTeamWrapper(BaseModel):
    """Wrapper layer: each item in ``leagues[0].teams`` is ``{team: {...}}``."""

    model_config = ConfigDict(extra="ignore")

    team: ESPNTeamEntry


class ESPNLeague(BaseModel):
    """A single league object nested under a sport."""

    model_config = ConfigDict(extra="ignore")

    teams: list[ESPNTeamWrapper]  # NOT list[ESPNSport] — see PATTERNS.md note


class ESPNSport(BaseModel):
    """Top-level sport wrapper; contains one or more leagues."""

    model_config = ConfigDict(extra="ignore")

    leagues: list[ESPNLeague]


class ESPNTeamsResponse(BaseModel):
    """Root envelope for the ESPN teams API response.

    Usage::

        raw = await http_client.get(url)
        response = ESPNTeamsResponse.model_validate(raw.json())
        teams = response.sports[0].leagues[0].teams
    """

    model_config = ConfigDict(extra="ignore")

    sports: list[ESPNSport]
