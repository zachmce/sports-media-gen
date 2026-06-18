"""Wave 0 scaffold for Phase 14 provider seam tests.

Tests for the DataProvider Protocol, LEAGUE_REGISTRY, KNOWN_LEAGUES, and the
SSRF gate documented in T-i3r-01 (NCAA sportbanner dict-lookup-as-gate).

These tests import symbols that do not exist until Plans 02–03 land.  The
module is guarded with ``pytest.importorskip`` so it collects and skips
cleanly during Wave 0, then turns into real assertions once the
``matchup_thumbs.providers`` package exists.
"""

from __future__ import annotations

import pytest

# Guard: skip the whole module if the providers package is not yet installed.
# This keeps Wave 0 CI green while Plans 02-03 are not yet merged.
providers_registry = pytest.importorskip("matchup_thumbs.providers.registry")

from matchup_thumbs.espn.client import LEAGUE_ENDPOINTS  # noqa: E402 (guarded above)
from matchup_thumbs.providers.espn import (  # noqa: E402
    _NCAA_SPORTBANNER_SPORTS,
    ESPNProvider,
)
from matchup_thumbs.providers.protocol import DataProvider  # noqa: E402
from matchup_thumbs.providers.registry import (  # noqa: E402
    KNOWN_LEAGUES,
    LEAGUE_REGISTRY,
)

# ---------------------------------------------------------------------------
# Registry / KNOWN_LEAGUES invariants
# ---------------------------------------------------------------------------

_EXPECTED_SLUGS: frozenset[str] = frozenset(
    {"nba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"}
)


def test_known_leagues_derives_from_registry() -> None:
    """D-10: KNOWN_LEAGUES must equal frozenset(LEAGUE_REGISTRY.keys()).

    This is success criterion #4 for Phase 14.  If this fails after Plans
    02-03 land the registry wiring is broken.
    """
    assert frozenset(LEAGUE_REGISTRY.keys()) == KNOWN_LEAGUES


def test_known_leagues_has_six_slugs() -> None:
    """LEAGUE_REGISTRY covers exactly the 6 ESPN leagues."""
    assert KNOWN_LEAGUES == _EXPECTED_SLUGS


def test_known_leagues_matches_espn_endpoints() -> None:
    """Sanity: KNOWN_LEAGUES matches LEAGUE_ENDPOINTS (ESPN is the sole provider)."""
    assert frozenset(LEAGUE_ENDPOINTS.keys()) == KNOWN_LEAGUES


# ---------------------------------------------------------------------------
# ESPNProvider structural compatibility with DataProvider Protocol
# ---------------------------------------------------------------------------


def test_espn_provider_satisfies_protocol() -> None:
    """ESPNProvider is structurally compatible with DataProvider.

    The type annotation ``provider: DataProvider = ESPNProvider()`` is the
    mypy gate (--strict validates structural compatibility at the call site).
    At runtime we verify list_leagues() returns the 6 expected slugs.
    """
    provider: DataProvider = ESPNProvider()  # type: ignore[assignment]
    result = provider.list_leagues()
    assert frozenset(result) == _EXPECTED_SLUGS


# ---------------------------------------------------------------------------
# SSRF gate: _NCAA_SPORTBANNER_SPORTS dict-lookup-as-gate (T-i3r-01)
# ---------------------------------------------------------------------------


def test_ncaa_sportbanner_sports_is_gate() -> None:
    """D-12 / T-i3r-01: only ncaaf and ncaab are in the NCAA sportbanner map.

    An unknown slug must NOT be a key — the dict-lookup is the SSRF gate that
    ensures no user-supplied or ESPN-supplied string ever reaches the ncaa.com
    CDN URL.
    """
    assert set(_NCAA_SPORTBANNER_SPORTS.keys()) == {"ncaaf", "ncaab"}
    assert "xyz" not in _NCAA_SPORTBANNER_SPORTS
    assert "nba" not in _NCAA_SPORTBANNER_SPORTS
    assert "nfl" not in _NCAA_SPORTBANNER_SPORTS
    assert "mlb" not in _NCAA_SPORTBANNER_SPORTS
    assert "nhl" not in _NCAA_SPORTBANNER_SPORTS
