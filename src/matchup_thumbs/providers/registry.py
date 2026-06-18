"""Provider registry: LEAGUE_REGISTRY + KNOWN_LEAGUES.

Defines the runtime mapping from league slug → concrete ``DataProvider``
implementation (D-09) and derives ``KNOWN_LEAGUES`` from it (D-10).

``KNOWN_LEAGUES = frozenset(LEAGUE_REGISTRY.keys())`` is success criterion #4
for Phase 14: the SSRF slug-validation gate and resolver scoping now extend
automatically to every future provider added here.

Import-cycle safety (RESEARCH.md Pitfall 7)
-------------------------------------------
This module imports ONLY from ``providers/espn.py`` and ``providers/protocol.py``.
It MUST NOT import from ``seed.py`` or ``resolver.py``; those modules import
from this module, not the reverse.
"""

from __future__ import annotations

from .espn import ESPNProvider
from .protocol import DataProvider

# ---------------------------------------------------------------------------
# Shared ESPNProvider singleton (D-09)
# ---------------------------------------------------------------------------
# One instance covers all 6 ESPN leagues — the provider is stateless so a
# single object is both thread-safe and memory-efficient.
_espn: ESPNProvider = ESPNProvider()

# ---------------------------------------------------------------------------
# LEAGUE_REGISTRY: slug → DataProvider (D-09)
# ---------------------------------------------------------------------------
# All 6 ESPN slugs map to the same shared singleton.  When a second provider
# is added in a later phase, its slugs appear here alongside the ESPN ones.
LEAGUE_REGISTRY: dict[str, DataProvider] = {
    "nba": _espn,
    "nfl": _espn,
    "mlb": _espn,
    "nhl": _espn,
    "ncaaf": _espn,
    "ncaab": _espn,
}

# ---------------------------------------------------------------------------
# KNOWN_LEAGUES: frozenset derived from registry keys (D-10, criterion #4)
# ---------------------------------------------------------------------------
# This derivation is the key correctness and security guarantee: any slug
# that reaches a provider method must first be in KNOWN_LEAGUES, and
# KNOWN_LEAGUES tracks the registry — so future providers are automatically
# gated without any manual sync step.
KNOWN_LEAGUES: frozenset[str] = frozenset(LEAGUE_REGISTRY.keys())
