"""Provider registry: LEAGUE_REGISTRY + KNOWN_LEAGUES.

Defines the runtime mapping from league slug → concrete ``DataProvider``
implementation (D-09) and derives ``KNOWN_LEAGUES`` from it (D-10).

``KNOWN_LEAGUES = frozenset(LEAGUE_REGISTRY.keys())`` is success criterion #4
for Phase 14: the SSRF slug-validation gate and resolver scoping now extend
automatically to every future provider added here.

Import-cycle safety (RESEARCH.md Pitfall 7)
-------------------------------------------
This module imports ONLY from ``providers/espn.py``, ``providers/mlb.py``, and
``providers/protocol.py``.  It MUST NOT import from ``seed.py`` or
``resolver.py``; those modules import from this module, not the reverse.
"""

from __future__ import annotations

from .espn import ESPNProvider
from .mlb import MLBStatsProvider
from .protocol import DataProvider

# ---------------------------------------------------------------------------
# Shared provider singletons (D-09)
# ---------------------------------------------------------------------------
# Each provider is stateless so a single object is both thread-safe and
# memory-efficient.  All ESPN slugs share one instance; all MiLB slugs share
# one MLBStatsProvider instance.
_espn: ESPNProvider = ESPNProvider()
_mlb: MLBStatsProvider = MLBStatsProvider()

# ---------------------------------------------------------------------------
# LEAGUE_REGISTRY: slug → DataProvider (D-09)
# ---------------------------------------------------------------------------
# All 6 ESPN slugs map to the ESPN singleton; the 8 MiLB slugs (quick task
# 260716-ia6: Single-A hard-renamed to its game-thumbs-matching slug below,
# plus the new milb umbrella, milb-winter, milb-independent) map to the
# shared MLBStatsProvider singleton.  "milb" (umbrella, baseball minors) is
# distinct from "mlb" (ESPN majors) — they differ by one letter and both
# exist in this dict.  KNOWN_LEAGUES auto-derives to 14 (was 11).
LEAGUE_REGISTRY: dict[str, DataProvider] = {
    "nba": _espn,
    "nfl": _espn,
    "mlb": _espn,
    "nhl": _espn,
    "ncaaf": _espn,
    "ncaab": _espn,
    "milb": _mlb,  # umbrella — logical union of the 4 affiliate levels below
    "milb-aaa": _mlb,  # Phase 15 — Triple-A
    "milb-aa": _mlb,  # Phase 15 — Double-A
    "milb-high-a": _mlb,  # Phase 15 — High-A
    "milb-a": _mlb,  # Single-A: hard-renamed slug, no compatibility alias
    "milb-rookie": _mlb,  # Phase 16 — Rookie (DSL/ACL/FCL, sportId=16)
    "milb-winter": _mlb,  # Winter Leagues, sportId=17
    "milb-independent": _mlb,  # Independent Leagues, sportId=23
}

# ---------------------------------------------------------------------------
# KNOWN_LEAGUES: frozenset derived from registry keys (D-10, criterion #4)
# ---------------------------------------------------------------------------
# This derivation is the key correctness and security guarantee: any slug
# that reaches a provider method must first be in KNOWN_LEAGUES, and
# KNOWN_LEAGUES tracks the registry — so future providers are automatically
# gated without any manual sync step.
# KNOWN_LEAGUES now auto-derives to 14 slugs (6 ESPN + 8 MiLB) — SSRF gate +
# resolver scoping extends automatically (D-10, D-16).
KNOWN_LEAGUES: frozenset[str] = frozenset(LEAGUE_REGISTRY.keys())
