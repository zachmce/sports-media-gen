"""Canonical provider-neutral Pydantic v2 models.

These are the canonical return types for the DataProvider protocol.  All
field names are snake_case — no ESPN camelCase leaks here (PROV-01 / D-03 /
D-05).  ``extra="ignore"`` is set on all models: these are outbound canonical
types used by seed.py for persistence; unknown extra fields are tolerated
silently.  Fail-loudly validation (``Field(min_length=...)``) belongs in
``espn/models.py`` at ESPN parse time, not here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProviderTeam(BaseModel):
    """Provider-neutral representation of a single team.

    Field set equals exactly the D-05 canonical surface — the persistence
    fields that seed.py writes to the teams table — nothing more.

    All identifiers are opaque strings: ``provider_id`` is whatever the
    provider considers its primary team key (e.g. ESPN's numeric string id).
    Colors are stored as raw hex WITHOUT a leading ``#`` — seed.py owns the
    single-``#`` normalization at write time.
    """

    model_config = ConfigDict(extra="ignore")

    # Required fields — no defaults; ValidationError if missing
    provider_id: str  # opaque provider-specific ID (D-07)
    slug: str  # e.g. "los-angeles-lakers"; upsert key with league_id
    display_name: str  # e.g. "Los Angeles Lakers"
    abbreviation: str  # e.g. "LAL"
    short_display_name: str  # e.g. "Lakers"
    location: str  # city/region; e.g. "Los Angeles"
    name: str  # mascot name; e.g. "Lakers"

    # Optional fields
    primary_color: str | None = None  # raw hex WITHOUT '#'; e.g. "552583"
    secondary_color: str | None = None  # secondary hex WITHOUT '#'
    logo_url: str | None = None  # canonical logo CDN URL
    logo_variants: dict[str, str] = {}  # variant key → href
    is_active: bool = True
    # Provider-supplied alias variants beyond what generate_aliases() derives.
    # Used by milb-rookie to emit prefixed complex variants (e.g. "dsl yankees").
    # seed.py loops: generate_aliases(team) + team.extra_aliases (D-07).
    # All other leagues leave this empty — zero behavioral change for them.
    extra_aliases: list[str] = Field(default_factory=list)


class ProviderLogoShield(BaseModel):
    """Provider-neutral league shield (logo) data.

    Carries the pre-fetched bytes so seed.py can persist and Redis-warm league
    logos without knowing whether the source was ESPN or another provider.
    ``bytes_default`` and ``bytes_dark`` let seed.py stay fully provider-neutral:
    it just writes what the provider gives it.
    """

    model_config = ConfigDict(extra="ignore")

    logo_url: str | None  # canonical shield URL (None if unavailable)
    variant_map: dict[str, str]  # variant key → href
    bytes_default: bytes | None  # pre-fetched default-variant bytes
    bytes_dark: bytes | None  # pre-fetched dark-variant bytes (None if same as default)
