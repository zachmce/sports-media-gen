"""Listing route tests — GET /sports, GET /leagues and GET /{league}/teams.

Tests use TestClient with a patched stub lifespan that injects a mock pool
returning seeded fixture rows — no live Postgres required.  Covers API-03,
API-04, SPORT-03, SPORT-04.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchup_thumbs.main import app

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_LEAGUE_ROWS: list[dict[str, Any]] = [
    {"slug": "mlb", "display_name": "MLB", "sport": "baseball"},
    {"slug": "nba", "display_name": "NBA", "sport": "basketball"},
    {"slug": "ncaab", "display_name": "NCAA Basketball", "sport": "basketball"},
    {"slug": "ncaaf", "display_name": "NCAA Football", "sport": "football"},
    {"slug": "nfl", "display_name": "NFL", "sport": "football"},
    {"slug": "nhl", "display_name": "NHL", "sport": "hockey"},
]

_NBA_TEAM_ROWS: list[dict[str, Any]] = [
    {
        "slug": "los-angeles-clippers",
        "display_name": "Los Angeles Clippers",
        "abbreviation": "LAC",
        "aliases": ["lac", "losangelesclippers"],
    },
    {
        "slug": "los-angeles-lakers",
        "display_name": "Los Angeles Lakers",
        "abbreviation": "LAL",
        "aliases": ["lal", "lakers", "losangeles", "losangeleslakers"],
    },
]

# Simulates LEFT JOIN output from the GET /sports query — one row per
# (sport, league) pair, with aliased column names matching the SQL aliases
# (sport_slug, sport_display_name, league_slug, league_display_name).
# Covers 4 sports; baseball has two leagues (milb-aaa, mlb) slug-ordered and
# hockey has one (nhl) — exercises multi-league grouping and ordering (D-04).
_SPORTS_JOIN_ROWS: list[dict[str, Any]] = [
    {
        "sport_slug": "baseball",
        "sport_display_name": "Baseball",
        "league_slug": "milb-aaa",
        "league_display_name": "Triple-A",
    },
    {
        "sport_slug": "baseball",
        "sport_display_name": "Baseball",
        "league_slug": "mlb",
        "league_display_name": "MLB",
    },
    {
        "sport_slug": "basketball",
        "sport_display_name": "Basketball",
        "league_slug": "nba",
        "league_display_name": "NBA",
    },
    {
        "sport_slug": "basketball",
        "sport_display_name": "Basketball",
        "league_slug": "ncaab",
        "league_display_name": "NCAA Basketball",
    },
    {
        "sport_slug": "football",
        "sport_display_name": "Football",
        "league_slug": "ncaaf",
        "league_display_name": "NCAA Football",
    },
    {
        "sport_slug": "football",
        "sport_display_name": "Football",
        "league_slug": "nfl",
        "league_display_name": "NFL",
    },
    {
        "sport_slug": "hockey",
        "sport_display_name": "Hockey",
        "league_slug": "nhl",
        "league_display_name": "NHL",
    },
]

# Simulates the all-NULL LEFT JOIN row for a sport with zero leagues (D-05).
# Used to exercise the null-league coalesce to leagues: [] in list_sports.
_SPORTS_EMPTY_ROWS: list[dict[str, Any]] = [
    {
        "sport_slug": "esports",
        "sport_display_name": "Esports",
        "league_slug": None,
        "league_display_name": None,
    },
]


# ---------------------------------------------------------------------------
# Stub lifespan helpers
# ---------------------------------------------------------------------------


def _make_cursor(
    fetchone_result: Any, fetchall_result: list[dict[str, Any]]
) -> MagicMock:
    """Build a MagicMock cursor supporting the async context manager protocol.

    psycopg3's ``conn.cursor()`` is a *sync* call returning an object that
    supports ``async with``.  ``AsyncMock`` would make it a coroutine (awaitable),
    which is wrong — we need a plain ``MagicMock`` with ``__aenter__``/``__aexit__``
    set to ``AsyncMock``s so ``async with conn.cursor() as cur`` works.
    """
    cur = MagicMock()
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=fetchone_result)
    cur.fetchall = AsyncMock(return_value=fetchall_result)
    return cur


def _make_conn(cursor: MagicMock) -> MagicMock:
    """Build a MagicMock connection supporting the async context manager protocol."""
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    # cursor() is a sync call returning the cursor context manager
    conn.cursor.return_value = cursor
    # row_factory is a settable attribute
    conn.row_factory = None
    return conn


def _make_mock_pool(
    league_rows: list[dict[str, Any]],
    team_rows: list[dict[str, Any]] | None,
) -> MagicMock:
    """Build a MagicMock pool whose cursor returns ``league_rows`` then ``team_rows``.

    For /leagues: fetchall() → league_rows.
    For /{league}/teams: fetchone() → {"id": 1} (league exists), fetchall() → team_rows.
    """
    if team_rows is not None:
        cur = _make_cursor(fetchone_result={"id": 1}, fetchall_result=team_rows)
    else:
        cur = _make_cursor(fetchone_result=None, fetchall_result=league_rows)

    conn = _make_conn(cur)

    pool = MagicMock()
    pool.connection.return_value = conn
    return pool


def _make_unknown_league_pool() -> MagicMock:
    """Build a pool whose fetchone() always returns None (league not found)."""
    cur = _make_cursor(fetchone_result=None, fetchall_result=[])
    conn = _make_conn(cur)
    pool = MagicMock()
    pool.connection.return_value = conn
    return pool


def _stub_lifespan_with_pool(pool: MagicMock):  # type: ignore[no-untyped-def]
    """Return an asynccontextmanager lifespan that injects *pool* onto app.state."""

    @asynccontextmanager
    async def _lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        redis_client = MagicMock()
        redis_client.aclose = AsyncMock()
        http_client = MagicMock()
        http_client.aclose = AsyncMock()

        fastapi_app.state.db_pool = pool
        fastapi_app.state.redis = redis_client
        fastapi_app.state.http_client = http_client
        yield

    return _lifespan


# ---------------------------------------------------------------------------
# Per-test client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def leagues_client() -> Generator[TestClient]:
    """TestClient configured to return six league rows from GET /leagues."""
    pool = _make_mock_pool(league_rows=_LEAGUE_ROWS, team_rows=None)
    original = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan_with_pool(pool)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


@pytest.fixture
def teams_client() -> Generator[TestClient]:
    """TestClient configured to return NBA team rows from GET /{league}/teams."""
    pool = _make_mock_pool(league_rows=_LEAGUE_ROWS, team_rows=_NBA_TEAM_ROWS)
    original = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan_with_pool(pool)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


@pytest.fixture
def unknown_league_client() -> Generator[TestClient]:
    """TestClient configured so any league lookup returns 404 (no matching row)."""
    pool = _make_unknown_league_pool()
    original = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan_with_pool(pool)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


@pytest.fixture
def sports_client() -> Generator[TestClient]:
    """TestClient configured to return _SPORTS_JOIN_ROWS from GET /sports.

    The mock pool cursor returns _SPORTS_JOIN_ROWS from fetchall(), simulating
    the LEFT JOIN output (one row per sport+league pair) — four sports, baseball
    with two leagues, hockey with one.
    """
    cur = _make_cursor(fetchone_result=None, fetchall_result=_SPORTS_JOIN_ROWS)
    conn = _make_conn(cur)
    pool = MagicMock()
    pool.connection.return_value = conn

    original = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan_with_pool(pool)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


@pytest.fixture
def sports_empty_client() -> Generator[TestClient]:
    """TestClient configured to return _SPORTS_EMPTY_ROWS from GET /sports.

    Simulates a sport with zero leagues (all-NULL LEFT JOIN row) to exercise
    the null-league coalesce to leagues: [] (D-05, RESEARCH Pitfall 2).
    """
    cur = _make_cursor(fetchone_result=None, fetchall_result=_SPORTS_EMPTY_ROWS)
    conn = _make_conn(cur)
    pool = MagicMock()
    pool.connection.return_value = conn

    original = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan_with_pool(pool)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_listing_leagues(leagues_client: TestClient) -> None:
    """API-04: GET /leagues returns a list of league objects.

    Each item must have slug, display_name, and sport fields.
    The six expected league slugs (nba, nfl, mlb, nhl, ncaaf, ncaab) must
    all appear in the response.  Also confirms that the mlb entry carries
    sport='baseball' — proving the handler's dict-key mapping works with
    the FK-join-sourced sport value (SPORT-04, D-07).
    """
    resp = leagues_client.get("/leagues")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data, list)

    # Every item must have the required fields
    for item in data:
        assert "slug" in item
        assert "display_name" in item
        assert "sport" in item

    slugs = {item["slug"] for item in data}
    assert {"nba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"} <= slugs

    # Confirm the FK-sourced sport value for a specific league (SPORT-04)
    mlb_items = [item for item in data if item["slug"] == "mlb"]
    assert len(mlb_items) == 1
    assert mlb_items[0]["sport"] == "baseball"


def test_listing_teams(teams_client: TestClient) -> None:
    """API-03: GET /nba/teams returns slug, display_name, abbreviation, aliases.

    For a known league, each team item must have a non-empty aliases list.
    """
    resp = teams_client.get("/nba/teams")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0

    for item in data:
        assert "slug" in item
        assert "display_name" in item
        assert "abbreviation" in item
        assert "aliases" in item
        assert isinstance(item["aliases"], list)

    # Confirm Lakers and Clippers are present
    slugs = {item["slug"] for item in data}
    assert "los-angeles-lakers" in slugs
    assert "los-angeles-clippers" in slugs


def test_listing_unknown_league_404(unknown_league_client: TestClient) -> None:
    """GET /zzz/teams is 404 with a JSON body containing the league key."""
    resp = unknown_league_client.get("/zzz/teams")
    assert resp.status_code == 404

    body = resp.json()
    # FastAPI wraps HTTPException detail under "detail"
    detail = body.get("detail", body)
    assert "league" in detail
    assert detail["league"] == "zzz"


def test_listing_sports(sports_client: TestClient) -> None:
    """SPORT-03: GET /sports returns a list of sport objects.

    Each sport has slug, display_name, and a leagues list.  Nested league
    objects have only slug and display_name — no sport field (D-01).
    Baseball has two leagues; hockey has one.
    """
    resp = sports_client.get("/sports")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 4

    for sport in data:
        assert "slug" in sport
        assert "display_name" in sport
        assert "leagues" in sport
        assert isinstance(sport["leagues"], list)
        for league in sport["leagues"]:
            assert "slug" in league
            assert "display_name" in league
            # Nested league objects must NOT carry a redundant sport field (D-01)
            assert "sport" not in league

    # baseball has two nested leagues; hockey has one
    baseball = next(s for s in data if s["slug"] == "baseball")
    assert len(baseball["leagues"]) == 2

    hockey = next(s for s in data if s["slug"] == "hockey")
    assert len(hockey["leagues"]) == 1


def test_listing_sports_ordering(sports_client: TestClient) -> None:
    """SPORT-03 / D-04: GET /sports returns sports and leagues ordered by slug."""
    resp = sports_client.get("/sports")
    assert resp.status_code == 200

    data = resp.json()

    # Top-level sports are slug-ordered
    sport_slugs = [s["slug"] for s in data]
    assert sport_slugs == ["baseball", "basketball", "football", "hockey"]

    # Baseball's nested leagues are slug-ordered (milb-aaa < mlb)
    baseball = next(s for s in data if s["slug"] == "baseball")
    league_slugs = [lg["slug"] for lg in baseball["leagues"]]
    assert league_slugs == ["milb-aaa", "mlb"]


def test_listing_sports_empty_leagues(sports_empty_client: TestClient) -> None:
    """SPORT-03 / D-05: A sport with zero leagues returns leagues: [].

    The all-NULL LEFT JOIN row (league_slug=None) must be coalesced to an
    empty list, not appended as a null entry (RESEARCH Pitfall 2).
    """
    resp = sports_empty_client.get("/sports")
    assert resp.status_code == 200

    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1

    sport = data[0]
    assert sport["slug"] == "esports"
    assert sport["display_name"] == "Esports"
    assert sport["leagues"] == []
