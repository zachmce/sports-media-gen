"""Listing route tests — GET /leagues and GET /{league}/teams (API-03, API-04).

Tests use TestClient with a patched stub lifespan that injects a mock pool
returning seeded fixture rows — no live Postgres required.
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_listing_leagues(leagues_client: TestClient) -> None:
    """API-04: GET /leagues returns a list of league objects.

    Each item must have slug, display_name, and sport fields.
    The six expected league slugs (nba, nfl, mlb, nhl, ncaaf, ncaab) must
    all appear in the response.
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
