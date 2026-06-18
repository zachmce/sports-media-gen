"""Wave 0 scaffold for Phase 14 + Phase 15 provider seam tests.

Tests for the DataProvider Protocol, LEAGUE_REGISTRY, KNOWN_LEAGUES, and the
SSRF gate documented in T-i3r-01 (NCAA sportbanner dict-lookup-as-gate) and
T-15-XSS (MiLB SVG variant never-rasterized invariant).

Phase 14: ESPN provider seam, KNOWN_LEAGUES, SSRF gate.
Phase 15: MLBStatsProvider scaffold tests (guarded by importorskip so they skip
until providers/mlb.py lands in Wave 1).

The module is guarded with ``pytest.importorskip`` so it collects and skips
cleanly when the providers package is absent, then becomes real assertions once
the package exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

# The 6 ESPN slugs — unchanged by Phase 15 (D-18 regression safety)
_EXPECTED_ESPN_SLUGS: frozenset[str] = frozenset(
    {"nba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"}
)

# All 10 slugs post-Phase-15: 6 ESPN + 4 MiLB
_EXPECTED_ALL_SLUGS: frozenset[str] = frozenset(
    {
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "ncaaf",
        "ncaab",
        "milb-aaa",
        "milb-aa",
        "milb-high-a",
        "milb-single-a",
    }
)


def test_known_leagues_derives_from_registry() -> None:
    """D-10: KNOWN_LEAGUES must equal frozenset(LEAGUE_REGISTRY.keys()).

    This is success criterion #4 for Phase 14.  If this fails after Plans
    02-03 land the registry wiring is broken.
    """
    assert frozenset(LEAGUE_REGISTRY.keys()) == KNOWN_LEAGUES


def test_known_leagues_has_ten_slugs() -> None:
    """LEAGUE_REGISTRY covers all 10 slugs (6 ESPN + 4 MiLB) post-Phase-15.

    Updated from test_known_leagues_has_six_slugs (D-18 Pitfall 5): adding 4
    MiLB slugs via LEAGUE_REGISTRY makes KNOWN_LEAGUES auto-grow to 10.  This
    test will be RED (KNOWN_LEAGUES still == 6 ESPN slugs) until Wave 1 lands
    MLBStatsProvider in registry.py.  At that point it turns GREEN.
    """
    assert KNOWN_LEAGUES == _EXPECTED_ALL_SLUGS


def test_known_leagues_matches_espn_endpoints() -> None:
    """Sanity: all ESPN LEAGUE_ENDPOINTS slugs are in KNOWN_LEAGUES (subset check).

    Changed from == to issubset: Phase 15 adds 4 MiLB slugs not in
    LEAGUE_ENDPOINTS.  ESPNProvider coverage is still fully validated — all
    ESPN slugs must remain present (D-18, RESEARCH Open Question 2).
    """
    assert frozenset(LEAGUE_ENDPOINTS.keys()).issubset(KNOWN_LEAGUES)


# ---------------------------------------------------------------------------
# ESPNProvider structural compatibility with DataProvider Protocol
# ---------------------------------------------------------------------------


def test_espn_provider_satisfies_protocol() -> None:
    """ESPNProvider is structurally compatible with DataProvider.

    The type annotation ``provider: DataProvider = ESPNProvider()`` is the
    mypy gate (--strict validates structural compatibility at the call site).
    At runtime we verify list_leagues() returns the 6 ESPN slugs (unchanged
    by Phase 15 — D-18 regression safety).
    """
    provider: DataProvider = ESPNProvider()  # type: ignore[assignment]
    result = provider.list_leagues()
    assert frozenset(result) == _EXPECTED_ESPN_SLUGS


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


# ---------------------------------------------------------------------------
# Phase 15: MLBStatsProvider tests (per-test importorskip so ESPN tests stay)
# ---------------------------------------------------------------------------
# These tests use per-function importorskip so the ESPN tests above continue
# to run while providers/mlb.py doesn't yet exist (Wave 0 → Wave 1 transition).
# Once MLBStatsProvider lands (Wave 1), all guards resolve and assertions run.
# ---------------------------------------------------------------------------

_MLB_SKIP_REASON = (
    "matchup_thumbs.providers.mlb not yet implemented (Phase 15 Wave 1). "
    "Test will run once MLBStatsProvider lands."
)


def test_mlb_provider_satisfies_protocol() -> None:
    """MILB-01: MLBStatsProvider is structurally compatible with DataProvider.

    Mirrors test_espn_provider_satisfies_protocol.
    list_leagues() returns the 4 MiLB slugs.
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]

    provider: DataProvider = _MLBStatsProvider()  # type: ignore[assignment]
    result = provider.list_leagues()
    assert frozenset(result) == frozenset(
        {"milb-aaa", "milb-aa", "milb-high-a", "milb-single-a"}
    )


def test_milb_sport_ids_is_gate() -> None:
    """D-03 / T-i3r-01: _MILB_SPORT_IDS is the SSRF gate for MiLB sport IDs.

    Only the 4 MiLB slugs are keys with exact integer sportId values.
    An unknown slug is NOT present — a dict-lookup KeyError is the gate that
    prevents any user/API-supplied string from reaching the MLB Stats API URL.
    Mirrors test_ncaa_sportbanner_sports_is_gate (T-i3r-01 pattern).
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MILB_SPORT_IDS: dict[str, int] = _mlb._MILB_SPORT_IDS  # type: ignore[attr-defined]

    assert set(_MILB_SPORT_IDS.keys()) == {
        "milb-aaa",
        "milb-aa",
        "milb-high-a",
        "milb-single-a",
    }
    assert _MILB_SPORT_IDS["milb-aaa"] == 11
    assert _MILB_SPORT_IDS["milb-aa"] == 12
    assert _MILB_SPORT_IDS["milb-high-a"] == 13
    assert _MILB_SPORT_IDS["milb-single-a"] == 14
    # Out-of-scope slugs must NOT be in the gate dict
    assert "milb-rookie" not in _MILB_SPORT_IDS
    assert "xyz" not in _MILB_SPORT_IDS


def test_mlb_fetch_teams_returns_provider_teams(httpx_mock: Any) -> None:
    """MILB-01: fetch_teams maps MLB Stats API JSON → ProviderTeam list.

    Updated for D-19/D-20 (15-06): logo_url is now the SVG primary mark (.svg),
    and primary_color is palette-extracted from the rasterized SVG (not None).

    Uses pytest-httpx to intercept the statsapi.mlb.com call AND all SVG CDN
    fetches (one per team).  The offline mlb_512.svg fixture is returned for
    every SVG GET so palette extraction runs without network (cairosvg skipif
    guard applies: test is skipped when libcairo2 is absent).
    """
    import asyncio
    import re as _re

    # Skip early (before registering mocks) if libcairo2 is absent locally.
    # Palette extraction requires cairosvg which raises OSError (not ImportError)
    # when libcairo2.so.2 is missing — same guard pattern as test_svg_raster.py.
    try:
        import cairosvg as _cs  # type: ignore[import-untyped]  # noqa: F401
    except OSError:
        pytest.skip("libcairo2 not installed locally — skipping raster-dependent test")

    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]
    _MILB_SPORT_IDS: dict[str, int] = _mlb._MILB_SPORT_IDS  # type: ignore[attr-defined]

    from matchup_thumbs.settings import settings as _settings

    fixture_path = Path(__file__).parent / "fixtures" / "mlb_aaa_response.json"
    fixture_data: dict[str, Any] = json.loads(fixture_path.read_text())

    svg_fixture_bytes = (
        Path(__file__).parent / "fixtures" / "mlb_512.svg"
    ).read_bytes()

    sport_id = _MILB_SPORT_IDS["milb-aaa"]
    stats_url = (
        f"{_settings.mlb_statsapi_base_url}/api/v1/teams"
        f"?sportId={sport_id}&activeStatus=Y"
    )
    # Mock the MLB Stats API response
    httpx_mock.add_response(url=stats_url, json=fixture_data)

    # Mock all SVG CDN fetches (one per team) with the offline fixture.
    # Pattern matches any URL on www.mlbstatic.com/team-logos/*.svg.
    # is_reusable=True so a single registration matches all 20+ team SVG GETs.
    httpx_mock.add_response(
        url=_re.compile(r"https://www\.mlbstatic\.com/team-logos/\d+\.svg"),
        content=svg_fixture_bytes,
        is_reusable=True,
    )

    import httpx as _httpx

    async def _run() -> list[Any]:
        async with _httpx.AsyncClient() as client:
            provider = _MLBStatsProvider()
            return await provider.fetch_teams(client, "milb-aaa")

    teams = asyncio.run(_run())

    assert len(teams) >= 10
    # Toledo Mud Hens is anchor team (id=512) — verify field mapping
    toledo = next((t for t in teams if t.provider_id == "512"), None)
    assert toledo is not None, "Toledo Mud Hens (provider_id='512') not found"
    assert toledo.display_name == "Toledo Mud Hens"
    assert toledo.abbreviation == "TOL"
    assert toledo.location == "Toledo"
    assert toledo.name == "Mud Hens"
    assert toledo.slug == "toledo-mud-hens"
    # D-20: primary_color is now palette-extracted from rasterized SVG (not None)
    assert toledo.primary_color is not None, (
        "Expected palette-extracted primary_color for MiLB team (D-20). "
        "Got None — check SVG fixture has opaque non-white pixels."
    )
    # D-19: logo_url is the SVG primary mark (not spot PNG)
    assert toledo.logo_url is not None
    assert toledo.logo_url.endswith(".svg"), (
        f"Expected logo_url to end with '.svg' (D-19), got {toledo.logo_url!r}"
    )


def test_mlb_logo_url_and_variants_mapping(httpx_mock: Any) -> None:
    """MILB-04: logo_url is SVG primary mark; logo_variants has 'spot' + 'svg' keys.

    Updated for D-19/D-21 (15-06):
    - logo_url is now the SVG primary-mark URL (ends with .svg), NOT the spot PNG.
    - logo_variants carries BOTH 'spot' (spot PNG) and 'svg' (SVG mark) for provenance.
    - No 'default' or 'dark' key (those are ESPN-specific).
    - The loader chain never selects 'spot' or 'svg' for direct rendering (T-15-XSS).
    """
    import asyncio
    import re as _re

    # Skip early (before registering mocks) if libcairo2 is absent locally.
    try:
        import cairosvg as _cs  # type: ignore[import-untyped]  # noqa: F401
    except OSError:
        pytest.skip("libcairo2 not installed locally — skipping raster-dependent test")

    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]
    _MILB_SPORT_IDS: dict[str, int] = _mlb._MILB_SPORT_IDS  # type: ignore[attr-defined]

    from matchup_thumbs.settings import settings as _settings

    fixture_path = Path(__file__).parent / "fixtures" / "mlb_aaa_response.json"
    fixture_data: dict[str, Any] = json.loads(fixture_path.read_text())

    svg_fixture_bytes = (
        Path(__file__).parent / "fixtures" / "mlb_512.svg"
    ).read_bytes()

    sport_id = _MILB_SPORT_IDS["milb-aaa"]
    stats_url = (
        f"{_settings.mlb_statsapi_base_url}/api/v1/teams"
        f"?sportId={sport_id}&activeStatus=Y"
    )
    httpx_mock.add_response(url=stats_url, json=fixture_data)

    # Mock all per-team SVG GET requests with the offline fixture bytes.
    httpx_mock.add_response(
        url=_re.compile(r"https://www\.mlbstatic\.com/team-logos/\d+\.svg"),
        content=svg_fixture_bytes,
        is_reusable=True,
    )

    import httpx as _httpx

    async def _run() -> list[Any]:
        async with _httpx.AsyncClient() as client:
            provider = _MLBStatsProvider()
            return await provider.fetch_teams(client, "milb-aaa")

    teams = asyncio.run(_run())
    assert teams, "Expected at least one team"

    team = teams[0]
    # D-19: logo_url is the SVG primary mark (ends with .svg, NOT /spots/500)
    assert team.logo_url is not None
    assert team.logo_url.endswith(".svg"), (
        f"Expected logo_url to end with '.svg' (D-19), got {team.logo_url!r}"
    )
    # D-21: logo_variants must have BOTH 'spot' and 'svg' keys
    assert team.logo_variants is not None
    assert "spot" in team.logo_variants, (
        f"Expected 'spot' key in logo_variants (D-21), "
        f"got keys: {list(team.logo_variants.keys())}"
    )
    assert team.logo_variants["spot"].endswith("/spots/500"), (
        f"Expected 'spot' URL ending in '/spots/500', "
        f"got {team.logo_variants['spot']!r}"
    )
    assert "svg" in team.logo_variants, (
        f"Expected 'svg' key in logo_variants (D-21), "
        f"got keys: {list(team.logo_variants.keys())}"
    )
    assert team.logo_variants["svg"].endswith(".svg"), (
        f"Expected 'svg' URL ending in '.svg', got {team.logo_variants['svg']!r}"
    )
    # T-15-XSS: No ESPN-style 'default' or 'dark' keys in logo_variants — the loader
    # chain (variant→dark→default→logo_url) must never select 'spot' or 'svg'.
    assert "default" not in team.logo_variants, (
        "logo_variants must not contain 'default' key for MiLB teams"
    )
    assert "dark" not in team.logo_variants, (
        "logo_variants must not contain 'dark' key for MiLB teams"
    )


def test_svg_variant_not_selected_by_loader() -> None:
    """MILB-04 / T-15-XSS: 'svg' in logo_variants is NEVER fetched at render time.

    The loader's fallback chain is: variant → 'dark' → 'default' → logo_url.
    When logo_variants = {'svg': svg_url} and variant='default' is requested,
    the chain misses 'default', then 'dark', then falls through to team['logo_url']
    (the spot PNG).  The SVG URL is never fetched.

    This test exercises the loader's _load_one_logo fallback logic directly
    by mocking Redis (miss) and the HTTP client.  It verifies the fetch URL
    is the spot PNG (logo_url), NOT the SVG URL from logo_variants['svg'].
    (T-15-XSS mitigated: SVG never rasterized.)

    NOTE: This test does NOT require matchup_thumbs.providers.mlb — it drives
    the existing assets.loader directly with a synthetic MiLB-style team dict.
    It is NOT guarded by importorskip because loader.py already exists.
    """
    import asyncio
    import io as _io
    from unittest.mock import AsyncMock, MagicMock

    from PIL import Image as _Image

    from matchup_thumbs.assets.loader import _load_one_logo
    from matchup_thumbs.settings import settings as _settings

    spot_png_url = "https://midfield.mlbstatic.com/v1/team/512/spots/500"
    svg_url = "https://www.mlbstatic.com/team-logos/512.svg"

    team: dict[str, Any] = {
        "id": 1,
        "league_id": 99,
        "slug": "toledo-mud-hens",
        "display_name": "Toledo Mud Hens",
        "abbreviation": "TOL",
        "primary_color": None,
        "secondary_color": None,
        "logo_url": spot_png_url,           # spot PNG is the terminal fallback
        "provider_id": "512",
        "logo_variants": {"svg": svg_url},  # only 'svg' key — never a valid chain hit
    }

    fetched_urls: list[str] = []

    # Build a 1×1 white PNG in memory for the mock response
    buf = _io.BytesIO()
    _Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(buf, format="PNG")
    fake_png = buf.getvalue()

    async def _fake_get(url: str, **kwargs: Any) -> Any:
        fetched_urls.append(url)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = fake_png
        return mock_resp

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)  # cache miss
    redis_mock.set = AsyncMock()

    http_client = MagicMock()
    http_client.get = _fake_get

    async def _run() -> None:
        await _load_one_logo(
            team=team,
            redis=redis_mock,
            http_client=http_client,
            league="milb-aaa",
            settings=_settings,
            variant="default",
        )

    asyncio.run(_run())

    assert fetched_urls, "Expected at least one fetch call"
    for url in fetched_urls:
        assert url != svg_url, (
            f"SVG URL must NEVER be fetched at render time (T-15-XSS). "
            f"Got fetched_urls={fetched_urls}"
        )
    # Exactly the spot PNG (logo_url) should have been fetched
    assert spot_png_url in fetched_urls, (
        f"Expected spot PNG URL '{spot_png_url}' to be fetched as fallback. "
        f"Got fetched_urls={fetched_urls}"
    )
