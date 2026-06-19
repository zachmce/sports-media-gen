"""Image route tests — API-01 (5-seg general form).

Mocks:
  - ``matchup_thumbs.routes.images.resolve_league`` — controls league resolution
  - ``matchup_thumbs.routes.images.resolve`` — controls away/home resolution outcomes
  - ``matchup_thumbs.routes.images.render_pipeline`` — returns a RenderResult with
    a real PNG payload so post_cache_transform has something to work with

All tests use the shared ``client`` fixture (stub lifespan; no live DB/Redis needed).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from matchup_thumbs.render import CACHE_CONTROL_IMMUTABLE, RenderResult

try:
    from matchup_thumbs.resolver import LeagueResolution
except ImportError:
    LeagueResolution = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(w: int = 1280, h: int = 720) -> bytes:
    """Build a minimal real PNG so post_cache_transform can decode it."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (85, 37, 131)).save(buf, format="PNG")
    return buf.getvalue()


def _team(slug: str, league: str = "nba") -> dict[str, Any]:
    """Minimal TeamDict-compatible dict for mock return values."""
    return {
        "id": 1,
        "league_id": 1,
        "slug": slug,
        "display_name": slug.title(),
        "abbreviation": slug[:3].upper(),
        "primary_color": "#552583",
        "secondary_color": "#fdb927",
        "logo_url": None,
        "provider_id": "13",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hit_result() -> RenderResult:
    """A RenderResult with a real PNG and tier='hit'."""
    return RenderResult(png=_make_png_bytes(), tier="hit")


@pytest.fixture
def both_resolve_lakers_celtics(hit_result: RenderResult) -> Iterator[None]:
    """Patch resolve_league + resolve + render_pipeline for NBA happy path."""
    away = _team("lakers")
    home = _team("celtics")
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# API-01: 5-segment general form (migrated from 4-seg)
# ---------------------------------------------------------------------------


def test_image_route_200(client: TestClient, both_resolve_lakers_celtics: None) -> None:
    """GET /{sport}/{league}/{away}/{home}/{kind} returns 200 PNG (API-01)."""
    resp = client.get("/basketball/nba/lakers/celtics/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_image_route_cache_control_immutable(
    client: TestClient, both_resolve_lakers_celtics: None
) -> None:
    """Image response carries Cache-Control: public, max-age=2592000, immutable."""
    resp = client.get("/basketball/nba/lakers/celtics/thumb")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == CACHE_CONTROL_IMMUTABLE


def test_unknown_away_team_404(client: TestClient, hit_result: RenderResult) -> None:
    """Unresolvable away team returns 404 with structured D-07 body (API-01)."""
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(return_value=None),
        ),
    ):
        resp = client.get("/basketball/nba/zzz/celtics/thumb")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "team_not_found"
    assert body["detail"]["field"] == "away"
    assert body["detail"]["league"] == "nba"
    assert body["detail"]["input"] == "zzz"


def test_unknown_home_team_404(client: TestClient, hit_result: RenderResult) -> None:
    """Unresolvable home team returns 404 with structured D-07 body (API-01)."""
    away = _team("lakers")
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, None]),
        ),
    ):
        resp = client.get("/basketball/nba/lakers/zzz/thumb")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "team_not_found"
    assert body["detail"]["field"] == "home"
    assert body["detail"]["league"] == "nba"
    assert body["detail"]["input"] == "zzz"


def test_unknown_kind_400(client: TestClient, hit_result: RenderResult) -> None:
    """Unknown kind → UnknownGeneratorError → 400 unknown_generator (API-01)."""
    from matchup_thumbs.render import UnknownGeneratorError

    away = _team("lakers")
    home = _team("celtics")
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(side_effect=UnknownGeneratorError("bogus", 0)),
        ),
    ):
        resp = client.get("/basketball/nba/lakers/celtics/bogus")

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "unknown_generator"
    assert body["detail"]["kind"] == "bogus"
    assert "style" in body["detail"]


def test_webp_fmt(client: TestClient, hit_result: RenderResult) -> None:
    """?fmt=webp returns WebP response with correct content-type (API-01, OUT-01)."""
    away = _team("lakers")
    home = _team("celtics")
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
    ):
        resp = client.get("/basketball/nba/lakers/celtics/thumb?fmt=webp")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"


def test_width_clamp_route(client: TestClient, hit_result: RenderResult) -> None:
    """?w=N returns image at clamped width (API-01, OUT-02)."""
    away = _team("lakers")
    home = _team("celtics")
    lr = LeagueResolution(slug="nba", sport="basketball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
    ):
        resp = client.get("/basketball/nba/lakers/celtics/thumb?w=64")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# NCAA via general form (post-alias-removal)
# ---------------------------------------------------------------------------


def test_ncaaf_resolves_via_general_form(
    client: TestClient, hit_result: RenderResult
) -> None:
    """ncaaf resolves via the 5-seg general route (ROUTE-01, ROUTE-03)."""
    away = _team("alabama", "ncaaf")
    home = _team("auburn", "ncaaf")
    lr = LeagueResolution(slug="ncaaf", sport="football")
    resolve_mock = AsyncMock(side_effect=[away, home])
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=resolve_mock,
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
    ):
        resp = client.get("/football/ncaaf/alabama/auburn/thumb")

    assert resp.status_code == 200
    assert resolve_mock.call_args_list[0][0][0] == "ncaaf"


def test_old_ncaa_alias_path_gone(client: TestClient) -> None:
    """The /ncaa/football/... URL no longer maps to NCAA football (ROUTE-01).

    With the 5-seg route, /ncaa/football/alabama/auburn/thumb matches as
    sport=ncaa, league=football — but 'football' is not a valid league, so
    resolve_league returns None → 404 league_not_found. The key property
    (this URL does not serve NCAA football images) is preserved.
    """
    with patch(
        "matchup_thumbs.routes.images.resolve_league",
        new=AsyncMock(return_value=None),
    ):
        resp = client.get("/ncaa/football/alabama/auburn/thumb")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 18 Wave 0: new 5-seg route RED tests + migrated 404/mismatch tests
# ---------------------------------------------------------------------------


def test_5seg_image_route_200(
    client: TestClient, both_resolve_lakers_celtics: None
) -> None:
    """ROUTE-03: GET /{sport}/{league}/{away}/{home}/{kind} returns 200 PNG."""
    resp = client.get("/basketball/nba/lakers/celtics/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_old_4seg_route_404(client: TestClient) -> None:
    """ROUTE-05: old 4-seg URL /nba/lakers/celtics/thumb returns 404."""
    resp = client.get("/nba/lakers/celtics/thumb")
    assert resp.status_code == 404


def test_sport_mismatch_404(client: TestClient, hit_result: RenderResult) -> None:
    """ROUTE-04: sport/league mismatch returns 404 sport_mismatch."""
    lr = LeagueResolution(slug="mlb", sport="baseball")
    with patch(
        "matchup_thumbs.routes.images.resolve_league",
        new=AsyncMock(return_value=lr),
    ):
        resp = client.get("/football/mlb/nyy/bos/thumb")  # sport mismatch

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "sport_mismatch"
    assert body["detail"]["sport"] == "football"
    assert body["detail"]["league"] == "mlb"
    assert body["detail"]["expected_sport"] == "baseball"


def test_league_not_found_404(client: TestClient) -> None:
    """LALIAS-02: unresolvable league returns 404 league_not_found."""
    with patch(
        "matchup_thumbs.routes.images.resolve_league",
        new=AsyncMock(return_value=None),
    ):
        resp = client.get("/baseball/zzzznotaleague/nyy/bos/thumb")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "league_not_found"
    assert body["detail"]["input"] == "zzzznotaleague"


def test_5seg_alias_resolution(client: TestClient, hit_result: RenderResult) -> None:
    """LALIAS-02: 'triple-a' alias in URL resolves to milb-aaa canonical slug."""
    away = _team("buffalo-bisons", "milb-aaa")
    home = _team("scranton-wilkes-barre", "milb-aaa")
    lr = LeagueResolution(slug="milb-aaa", sport="baseball")
    with (
        patch(
            "matchup_thumbs.routes.images.resolve_league",
            new=AsyncMock(return_value=lr),
        ),
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
    ):
        resp = client.get(
            "/baseball/triple-a/buffalo-bisons/scranton-wilkes-barre/thumb"
        )

    assert resp.status_code == 200
