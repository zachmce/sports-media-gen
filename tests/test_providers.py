"""Wave 0 scaffold for Phase 14 + Phase 15 + Phase 16 provider seam tests.

Tests for the DataProvider Protocol, LEAGUE_REGISTRY, KNOWN_LEAGUES, and the
SSRF gate documented in T-i3r-01 (NCAA sportbanner dict-lookup-as-gate) and
T-15-XSS (MiLB SVG variant never-rasterized invariant).

Phase 14: ESPN provider seam, KNOWN_LEAGUES, SSRF gate.
Phase 15: MLBStatsProvider scaffold tests (guarded by importorskip so they skip
until providers/mlb.py lands in Wave 1).
Phase 16: Rookie complex-tag gate + fetch_teams("milb-rookie") tests.

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

# All 14 slugs post-quick-260716-ia6: 6 ESPN + 8 MiLB
_EXPECTED_ALL_SLUGS: frozenset[str] = frozenset(
    {
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "ncaaf",
        "ncaab",
        "milb",  # umbrella (quick-260716-ia6)
        "milb-aaa",
        "milb-aa",
        "milb-high-a",
        "milb-a",  # renamed from milb-single-a (quick-260716-ia6)
        "milb-rookie",  # Phase 16
        "milb-winter",  # quick-260716-ia6
        "milb-independent",  # quick-260716-ia6
    }
)


def test_known_leagues_derives_from_registry() -> None:
    """D-10: KNOWN_LEAGUES must equal frozenset(LEAGUE_REGISTRY.keys()).

    This is success criterion #4 for Phase 14.  If this fails after Plans
    02-03 land the registry wiring is broken.
    """
    assert frozenset(LEAGUE_REGISTRY.keys()) == KNOWN_LEAGUES


def test_known_leagues_has_fourteen_slugs() -> None:
    """LEAGUE_REGISTRY covers all 14 slugs (6 ESPN + 8 MiLB) post-quick-260716-ia6.

    Updated from test_known_leagues_has_eleven_slugs: adding the milb umbrella,
    milb-winter, and milb-independent (and renaming milb-single-a -> milb-a)
    makes KNOWN_LEAGUES grow from 11 to 14 (D-09).
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
    list_leagues() returns 8 slugs: the 7 sportId slugs plus "milb" (umbrella).
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]

    provider: DataProvider = _MLBStatsProvider()  # type: ignore[assignment]
    result = provider.list_leagues()
    assert frozenset(result) == frozenset(
        {
            "milb",
            "milb-aaa",
            "milb-aa",
            "milb-high-a",
            "milb-a",
            "milb-rookie",
            "milb-winter",
            "milb-independent",
        }
    )


def test_milb_sport_ids_is_gate() -> None:
    """D-03 / T-i3r-01: _MILB_SPORT_IDS is the SSRF gate for MiLB sport IDs.

    Exactly 7 keys with exact integer sportId values.  "milb-single-a" is gone
    (hard-renamed to "milb-a", no alias) and "milb" (the umbrella) is
    deliberately NOT a key — it has no sportId of its own (T-ia6-02).  An
    unknown slug is NOT present — a dict-lookup KeyError is the gate that
    prevents any user/API-supplied string from reaching the MLB Stats API URL.
    Mirrors test_ncaa_sportbanner_sports_is_gate (T-i3r-01 pattern).
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MILB_SPORT_IDS: dict[str, int] = _mlb._MILB_SPORT_IDS  # type: ignore[attr-defined]

    assert set(_MILB_SPORT_IDS.keys()) == {
        "milb-aaa",
        "milb-aa",
        "milb-high-a",
        "milb-a",
        "milb-rookie",
        "milb-winter",
        "milb-independent",
    }
    assert _MILB_SPORT_IDS["milb-aaa"] == 11
    assert _MILB_SPORT_IDS["milb-aa"] == 12
    assert _MILB_SPORT_IDS["milb-high-a"] == 13
    assert _MILB_SPORT_IDS["milb-a"] == 14
    assert _MILB_SPORT_IDS["milb-rookie"] == 16  # Phase 16 — Rookie: DSL+ACL+FCL
    assert _MILB_SPORT_IDS["milb-winter"] == 17
    assert _MILB_SPORT_IDS["milb-independent"] == 23
    # Out-of-scope slugs must NOT be in the gate dict
    assert "xyz" not in _MILB_SPORT_IDS
    assert "milb-single-a" not in _MILB_SPORT_IDS  # hard rename, no alias
    assert "milb" not in _MILB_SPORT_IDS  # umbrella has no sportId (T-ia6-02)
    assert 21 not in _MILB_SPORT_IDS.values(), (
        "sportId 21 ('Minor League Baseball') is a decoy returning COVID-era "
        "'Alternate Training Site' rows and must never be reachable."
    )


def test_milb_umbrella_feeders_fixed_and_gate_integrity() -> None:
    """T-ia6-01: _MILB_UMBRELLA_FEEDERS is fixed and every member is a gate key.

    Feeders are the 4 affiliate levels only — milb-rookie is deliberately
    excluded (game-thumbs' stated feeder list is only the 4 affiliate levels).
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MILB_SPORT_IDS: dict[str, int] = _mlb._MILB_SPORT_IDS  # type: ignore[attr-defined]
    _MILB_UMBRELLA_FEEDERS: tuple[str, ...] = _mlb._MILB_UMBRELLA_FEEDERS  # type: ignore[attr-defined]

    assert _MILB_UMBRELLA_FEEDERS == ("milb-aaa", "milb-aa", "milb-high-a", "milb-a")
    assert all(feeder in _MILB_SPORT_IDS for feeder in _MILB_UMBRELLA_FEEDERS), (
        "Every umbrella feeder must be a key of _MILB_SPORT_IDS (gate integrity)."
    )
    assert "milb-rookie" not in _MILB_UMBRELLA_FEEDERS


def test_mlb_fetch_teams_milb_umbrella_fans_out_four_feeders(httpx_mock: Any) -> None:
    """T-ia6-01/02/04: fetch_teams("milb") issues exactly 4 requests (11/12/13/14).

    Never sportId 16 (rookie) or 21 (decoy).  Returns the concatenated union.
    """
    import asyncio
    import re as _re

    try:
        import cairosvg as _cs  # type: ignore[import-untyped]  # noqa: F401
    except OSError:
        pytest.skip("libcairo2 not installed locally — skipping raster-dependent test")

    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]

    from matchup_thumbs.settings import settings as _settings

    fixture_path = Path(__file__).parent / "fixtures" / "mlb_aaa_response.json"
    fixture_data: dict[str, Any] = json.loads(fixture_path.read_text())
    svg_fixture_bytes = (
        Path(__file__).parent / "fixtures" / "mlb_512.svg"
    ).read_bytes()

    requested_sport_ids: list[int] = []

    def _stats_callback(request: Any) -> Any:
        import httpx as _httpx

        requested_sport_ids.append(int(request.url.params["sportId"]))
        return _httpx.Response(200, json=fixture_data)

    httpx_mock.add_callback(
        callback=_stats_callback,
        url=_re.compile(
            _re.escape(f"{_settings.mlb_statsapi_base_url}/api/v1/teams") + r"\?.*"
        ),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_re.compile(r"https://www\.mlbstatic\.com/team-logos/\d+\.svg"),
        content=svg_fixture_bytes,
        is_reusable=True,
    )

    import httpx as _httpx

    async def _run() -> list[Any]:
        async with _httpx.AsyncClient() as client:
            provider = _MLBStatsProvider()
            return await provider.fetch_teams(client, "milb")

    teams = asyncio.run(_run())

    assert requested_sport_ids == [11, 12, 13, 14], (
        f"Expected exactly sportIds [11, 12, 13, 14] in order, "
        f"got {requested_sport_ids}"
    )
    assert 16 not in requested_sport_ids
    assert 21 not in requested_sport_ids
    assert len(teams) == 4 * len(fixture_data["teams"])


def test_mlb_fetch_teams_winter_and_independent_no_complex_tag(
    httpx_mock: Any,
) -> None:
    """T-ia6: fetch_teams for milb-winter/milb-independent skip Rookie-only paths.

    extra_aliases == [] and no _MILB_COMPLEX_TAG_IDS consultation — both slugs
    take the plain _derive_mlb_slug path (is_rookie gate stays False).
    """
    import asyncio
    import re as _re

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

    for league_slug in ("milb-winter", "milb-independent"):
        sport_id = _MILB_SPORT_IDS[league_slug]
        stats_url = (
            f"{_settings.mlb_statsapi_base_url}/api/v1/teams"
            f"?sportId={sport_id}&activeStatus=Y"
        )
        httpx_mock.add_response(url=stats_url, json=fixture_data)
        httpx_mock.add_response(
            url=_re.compile(r"https://www\.mlbstatic\.com/team-logos/\d+\.svg"),
            content=svg_fixture_bytes,
            is_reusable=True,
        )

        import httpx as _httpx

        async def _run(slug: str = league_slug) -> list[Any]:
            async with _httpx.AsyncClient() as client:
                provider = _MLBStatsProvider()
                return await provider.fetch_teams(client, slug)

        teams = asyncio.run(_run())
        assert teams, f"Expected at least one team for {league_slug}"
        for team in teams:
            assert team.extra_aliases == [], (
                f"Expected no extra_aliases for {league_slug} team "
                f"{team.slug!r}, got {team.extra_aliases!r}"
            )


def test_mlb_fetch_teams_bogus_slug_still_raises_keyerror() -> None:
    """T-ia6: fetch_teams("bogus") still raises KeyError (SSRF gate intact)."""
    import asyncio

    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MLBStatsProvider = _mlb.MLBStatsProvider  # type: ignore[attr-defined]

    import httpx as _httpx

    async def _run() -> None:
        async with _httpx.AsyncClient() as client:
            provider = _MLBStatsProvider()
            await provider.fetch_teams(client, "bogus")

    with pytest.raises(KeyError):
        asyncio.run(_run())


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
    """MILB-04 / T-15-XSS: loader chain never selects 'svg' or 'spot' variant keys.

    Updated for D-19/D-21 (15-06): logo_url is now the SVG primary mark URL
    (terminal fallback — D-19).  logo_variants carries both 'spot' and 'svg' keys
    for provenance (D-21).

    The loader's fallback chain is: variant → 'dark' → 'default' → logo_url.
    With logo_variants = {'spot': ..., 'svg': ...} and variant='default' requested:
    - variants.get('default') → None (miss)
    - variants.get('dark')    → None (miss)
    - variants.get('default') → None (already tried)
    - falls through to team['logo_url'] (the SVG URL)
    The 'svg' and 'spot' keys are NEVER iterated (T-15-XSS preserved).

    The loader calls rasterize_svg_if_needed on the fetched bytes so the cached /
    decoded value is always PNG (D-19 seam B — lazy-fetch path).  rasterize is
    mocked to return fake PNG bytes so this test runs without libcairo2 locally.

    NOTE: This test does NOT require matchup_thumbs.providers.mlb — it drives
    assets.loader directly with a synthetic MiLB-style team dict.
    """
    import asyncio
    import io as _io
    from unittest.mock import AsyncMock, MagicMock, patch

    from PIL import Image as _Image

    from matchup_thumbs.assets.loader import _load_one_logo
    from matchup_thumbs.settings import settings as _settings

    spot_png_url = "https://midfield.mlbstatic.com/v1/team/512/spots/500"
    svg_url = "https://www.mlbstatic.com/team-logos/512.svg"

    # D-19: logo_url is now the SVG URL (terminal fallback after chain misses).
    # D-21: logo_variants carries both 'spot' and 'svg' provenance keys —
    # neither 'spot' nor 'svg' is in the (variant, 'dark', 'default') chain.
    team: dict[str, Any] = {
        "id": 1,
        "league_id": 99,
        "slug": "toledo-mud-hens",
        "display_name": "Toledo Mud Hens",
        "abbreviation": "TOL",
        "primary_color": None,
        "secondary_color": None,
        "logo_url": svg_url,  # D-19: SVG URL is now the terminal fallback
        "provider_id": "512",
        "logo_variants": {
            "spot": spot_png_url,  # D-21: spot PNG for provenance
            "svg": svg_url,  # D-21: SVG URL for provenance
        },
    }

    fetched_urls: list[str] = []

    # Build fake SVG bytes (minimal valid — just needs to look like SVG to the mock).
    fake_svg = b"<svg><rect/></svg>"

    # Build fake PNG bytes (PNG magic header) for the mock rasterize return value.
    buf = _io.BytesIO()
    _Image.new("RGBA", (1, 1), (0, 43, 92, 255)).save(buf, format="PNG")
    fake_png = buf.getvalue()

    async def _fake_get(url: str, **kwargs: Any) -> Any:
        fetched_urls.append(url)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = fake_svg  # loader receives SVG bytes from the terminal URL
        return mock_resp

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)  # cache miss
    redis_mock.set = AsyncMock()

    http_client = MagicMock()
    http_client.get = _fake_get

    rasterize_called_with: list[bytes] = []

    def _mock_rasterize(raw: bytes) -> bytes:
        """Capture calls to rasterize_svg_if_needed; return fake PNG bytes."""
        rasterize_called_with.append(raw)
        return fake_png

    async def _run() -> None:
        # The loader's lazy `from ..svg import rasterize_svg_if_needed` normally
        # fails when libcairo2 is absent (svg.py imports cairosvg at module level
        # which raises OSError).  We pre-inject a stub cairosvg module so svg.py
        # can be imported; then patch rasterize_svg_if_needed with our mock.
        import sys
        import types

        _cairosvg_available = True
        try:
            import cairosvg as _cs  # type: ignore[import-untyped]  # noqa: F401
        except OSError:
            _cairosvg_available = False

        if not _cairosvg_available:
            # Inject a minimal cairosvg stub so svg.py can be imported.
            stub = types.ModuleType("cairosvg")
            stub.svg2png = lambda **_kw: b""  # type: ignore[attr-defined]
            sys.modules.setdefault("cairosvg", stub)

        # Remove any cached svg module so the lazy import picks up freshly.
        sys.modules.pop("matchup_thumbs.svg", None)

        with patch(
            "matchup_thumbs.svg.rasterize_svg_if_needed", side_effect=_mock_rasterize
        ):
            await _load_one_logo(
                team=team,
                redis=redis_mock,
                http_client=http_client,
                league="milb-aaa",
                settings=_settings,
                variant="default",
            )

        # Clean up injected stub so other tests are not affected.
        if not _cairosvg_available:
            sys.modules.pop("cairosvg", None)
        sys.modules.pop("matchup_thumbs.svg", None)

    asyncio.run(_run())

    # T-15-XSS: chain never selects 'spot' or 'svg' variant keys — only logo_url
    # (the SVG URL) is fetched as the terminal fallback.
    assert fetched_urls, "Expected at least one fetch call"
    assert svg_url in fetched_urls, (
        f"Expected SVG URL '{svg_url}' to be fetched as terminal logo_url "
        f"fallback (D-19). Got fetched_urls={fetched_urls}"
    )
    # Neither 'spot' nor 'svg' key from logo_variants should trigger a direct fetch.
    # The chain only tries (variant, 'dark', 'default') — none equal 'spot'/'svg'.
    assert fetched_urls == [svg_url], (
        f"Expected only logo_url SVG fetch (chain skipped 'spot'/'svg' variant keys). "
        f"Got fetched_urls={fetched_urls}"
    )

    # D-19 seam B: loader called rasterize_svg_if_needed on the fetched SVG bytes.
    assert rasterize_called_with, (
        "Expected rasterize_svg_if_needed to be called in the loader's lazy-fetch path"
    )
    assert rasterize_called_with[0] == fake_svg, (
        "Expected rasterize_svg_if_needed to receive the fetched SVG bytes"
    )

    # Verify Redis was given PNG bytes (not SVG bytes) — D-19 seam B invariant.
    assert redis_mock.set.called, "Expected redis.set to be called (cache update)"
    cached_bytes: bytes = redis_mock.set.call_args[0][1]
    assert cached_bytes.startswith(b"\x89PNG"), (
        f"Expected PNG magic header in cached bytes (D-19). Got {cached_bytes[:4]!r}"
    )


# ---------------------------------------------------------------------------
# Phase 16: Rookie complex-tag gate + fetch_teams("milb-rookie") tests
# ---------------------------------------------------------------------------
# These tests reference _MILB_COMPLEX_TAG_IDS and the Rookie provider branch,
# both of which land in Plan 02 (providers/mlb.py).  They are intentionally
# RED until Plan 02 merges.  The per-function importorskip keeps ESPN tests
# collecting/passing in the interim.
# ---------------------------------------------------------------------------


def test_milb_complex_tag_ids_is_gate() -> None:
    """D-03 / T-i3r-01: _MILB_COMPLEX_TAG_IDS is the SSRF-safe complex-tag gate.

    Only the 3 known league.id integers (130=DSL, 121=ACL, 124=FCL) are keys.
    An unknown id returns None via .get() — the fixed-dict lookup prevents
    API-supplied integers from producing unexpected tag strings that could
    contaminate slug/alias derivation.
    Mirrors test_ncaa_sportbanner_sports_is_gate (T-i3r-01 pattern).
    """
    _mlb = pytest.importorskip("matchup_thumbs.providers.mlb", reason=_MLB_SKIP_REASON)
    _MILB_COMPLEX_TAG_IDS: dict[int, str] = _mlb._MILB_COMPLEX_TAG_IDS  # type: ignore[attr-defined]

    assert set(_MILB_COMPLEX_TAG_IDS.keys()) == {130, 121, 124}
    assert _MILB_COMPLEX_TAG_IDS[130] == "dsl"  # Dominican Summer League
    assert _MILB_COMPLEX_TAG_IDS[121] == "acl"  # Arizona Complex League
    assert _MILB_COMPLEX_TAG_IDS[124] == "fcl"  # Florida Complex League
    assert _MILB_COMPLEX_TAG_IDS.get(999) is None  # unknown id → no tag (T-i3r-01)


def test_mlb_rookie_fetch_teams_returns_provider_teams(httpx_mock: Any) -> None:
    """MILB-07: fetch_teams("milb-rookie") maps DSL/ACL/FCL teams to ProviderTeam.

    Verifies:
    - At least one team per complex (DSL/ACL/FCL) returned.
    - DSL team slug starts with "dsl-" (complex-tag prefix derivation).
    - DSL team extra_aliases contains at least one entry with "dsl".
    - logo_url ends with ".svg" (D-19 inherited by Rookie path).
    Uses mlb_rookie_response.json fixture (7 teams across DSL/ACL/FCL).
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

    fixture_path = Path(__file__).parent / "fixtures" / "mlb_rookie_response.json"
    fixture_data: dict[str, Any] = json.loads(fixture_path.read_text())
    svg_fixture_bytes = (
        Path(__file__).parent / "fixtures" / "mlb_512.svg"
    ).read_bytes()

    sport_id = _MILB_SPORT_IDS["milb-rookie"]
    stats_url = (
        f"{_settings.mlb_statsapi_base_url}/api/v1/teams"
        f"?sportId={sport_id}&activeStatus=Y"
    )
    # Mock the MLB Stats API Rookie response
    httpx_mock.add_response(url=stats_url, json=fixture_data)

    # Mock all per-team SVG CDN fetches with the offline fixture.
    # is_reusable=True so a single registration matches all 7 SVG GETs.
    httpx_mock.add_response(
        url=_re.compile(r"https://www\.mlbstatic\.com/team-logos/\d+\.svg"),
        content=svg_fixture_bytes,
        is_reusable=True,
    )

    import httpx as _httpx

    async def _run() -> list[Any]:
        async with _httpx.AsyncClient() as client:
            provider = _MLBStatsProvider()
            return await provider.fetch_teams(client, "milb-rookie")

    teams = asyncio.run(_run())
    assert len(teams) >= 3, (
        f"Expected at least 3 Rookie teams (one per complex), got {len(teams)}"
    )

    # Find a DSL team — verify slug prefix (D-05) and extra_aliases (D-06)
    dsl_team = next((t for t in teams if t.slug.startswith("dsl-")), None)
    assert dsl_team is not None, (
        "No DSL team found with slug starting 'dsl-'. "
        "Expected _derive_rookie_slug to emit 'dsl-{stripped}' slugs (D-05)."
    )
    assert any("dsl" in a for a in dsl_team.extra_aliases), (
        f"Expected at least one extra_alias containing 'dsl' on DSL team. "
        f"Got extra_aliases={dsl_team.extra_aliases!r} (D-06: prefixed alias variants)."
    )
    # D-19 inherited: Rookie logo_url is also the SVG primary mark
    assert dsl_team.logo_url is not None
    assert dsl_team.logo_url.endswith(".svg"), (
        f"Expected logo_url ending with '.svg' (D-19), got {dsl_team.logo_url!r}"
    )
