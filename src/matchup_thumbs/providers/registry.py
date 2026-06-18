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
# All 6 ESPN slugs map to the ESPN singleton; the 4 MiLB slugs map to the
# shared MLBStatsProvider singleton.  KNOWN_LEAGUES auto-derives to 10 (D-16).
LEAGUE_REGISTRY: dict[str, DataProvider] = {
    "nba":           _espn,
    "nfl":           _espn,
    "mlb":           _espn,
    "nhl":           _espn,
    "ncaaf":         _espn,
    "ncaab":         _espn,
    "milb-aaa":      _mlb,    # Phase 15 — Triple-A
    "milb-aa":       _mlb,    # Phase 15 — Double-A
    "milb-high-a":   _mlb,    # Phase 15 — High-A
    "milb-single-a": _mlb,    # Phase 15 — Single-A
}

# ---------------------------------------------------------------------------
# KNOWN_LEAGUES: frozenset derived from registry keys (D-10, criterion #4)
# ---------------------------------------------------------------------------
# This derivation is the key correctness and security guarantee: any slug
# that reaches a provider method must first be in KNOWN_LEAGUES, and
# KNOWN_LEAGUES tracks the registry — so future providers are automatically
# gated without any manual sync step.
# KNOWN_LEAGUES now auto-derives to 10 slugs — SSRF gate + resolver scoping
# extends automatically (D-10, D-16).
KNOWN_LEAGUES: frozenset[str] = frozenset(LEAGUE_REGISTRY.keys())
