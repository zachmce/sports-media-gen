"""Tests for /readyz readiness probe (API-06).

/healthz liveness tests live in test_app.py (API-05 — already passing).
These tests cover the /readyz endpoint which runs live concurrent checks
against Postgres and Redis, each bounded by settings.readyz_check_timeout.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchup_thumbs.main import app

# ---------------------------------------------------------------------------
# Helpers: custom stub lifespans for /readyz dependency simulation
# ---------------------------------------------------------------------------


def _make_pg_success_pool() -> MagicMock:
    """Return a pool mock where connection()/cursor()/execute() all succeed."""
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)
    cur.execute = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.cursor.return_value = cur
    pool.connection.return_value = conn
    return pool


def _make_pg_fail_pool() -> MagicMock:
    """Return a pool mock where connection() raises (Postgres is down)."""
    pool = MagicMock()
    pool.connection.side_effect = OSError("connection refused")
    return pool


def _make_redis_success() -> MagicMock:
    """Return a Redis mock where ping() succeeds."""
    redis = MagicMock()
    redis.aclose = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis


def _make_redis_fail() -> MagicMock:
    """Return a Redis mock where ping() raises (Redis is down)."""
    redis = MagicMock()
    redis.aclose = AsyncMock()
    redis.ping = AsyncMock(side_effect=OSError("connection refused"))
    return redis


@asynccontextmanager
async def _lifespan_both_up(test_app: FastAPI) -> AsyncIterator[None]:
    """Stub lifespan: Postgres succeeds, Redis succeeds."""
    test_app.state.db_pool = _make_pg_success_pool()
    test_app.state.redis = _make_redis_success()
    test_app.state.http_client = MagicMock()
    yield


@asynccontextmanager
async def _lifespan_pg_down(test_app: FastAPI) -> AsyncIterator[None]:
    """Stub lifespan: Postgres fails, Redis succeeds."""
    test_app.state.db_pool = _make_pg_fail_pool()
    test_app.state.redis = _make_redis_success()
    test_app.state.http_client = MagicMock()
    yield


@asynccontextmanager
async def _lifespan_redis_down(test_app: FastAPI) -> AsyncIterator[None]:
    """Stub lifespan: Postgres succeeds, Redis fails."""
    test_app.state.db_pool = _make_pg_success_pool()
    test_app.state.redis = _make_redis_fail()
    test_app.state.http_client = MagicMock()
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_readyz_200() -> None:
    """GET /readyz returns 200 {"status": "ready"} when both deps are up (API-06)."""
    original = app.router.lifespan_context
    app.router.lifespan_context = _lifespan_both_up
    try:
        with TestClient(app) as c:
            resp = c.get("/readyz")
    finally:
        app.router.lifespan_context = original

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_readyz_503_postgres_down() -> None:
    """GET /readyz returns 503 with postgres=False when Postgres is down (API-06)."""
    original = app.router.lifespan_context
    app.router.lifespan_context = _lifespan_pg_down
    try:
        with TestClient(app) as c:
            resp = c.get("/readyz")
    finally:
        app.router.lifespan_context = original

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["postgres"] is False
    assert body["redis"] is True


def test_readyz_503_redis_down() -> None:
    """GET /readyz returns 503 with redis=False when Redis is down (API-06)."""
    original = app.router.lifespan_context
    app.router.lifespan_context = _lifespan_redis_down
    try:
        with TestClient(app) as c:
            resp = c.get("/readyz")
    finally:
        app.router.lifespan_context = original

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["postgres"] is True
    assert body["redis"] is False
