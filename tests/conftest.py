"""Shared pytest fixtures for matchup-thumbs tests."""

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from matchup_thumbs.main import app
from matchup_thumbs.settings import Settings


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide a Settings instance with test DSN values via monkeypatched env.

    Note: this fixture constructs a *new* Settings instance from the patched
    env and returns it for direct inspection.  It does NOT affect the
    module-level ``settings`` singleton already consumed by ``main.py``.
    Tests that need to verify Settings parsing should use this fixture; tests
    that need to interact with the running app via ``client`` use the stub
    lifespan in that fixture instead.
    """
    monkeypatch.setenv(
        "POSTGRES_DSN",
        "postgresql+psycopg://matchup:matchup@localhost:5432/matchup_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    return Settings()


@asynccontextmanager
async def _stub_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan stub that injects mock DB pool and Redis without real connections.

    Allows the app tests (health route, import smoke) to run without a live
    Postgres or Redis.  DB-integration tests live in ``test_migrations.py``
    and use a separate skip-guard mechanism.
    """
    pool = MagicMock()
    pool.close = AsyncMock()
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()

    app.state.db_pool = pool
    app.state.redis = redis_client
    yield


@pytest.fixture
def client() -> Generator[TestClient]:
    """Yield a TestClient backed by a stub lifespan (no live DB/Redis required).

    The real lifespan is replaced for the duration of the test so that
    ``uv run pytest -q`` passes without any external services running.
    DB-integration tests are gated separately in ``test_migrations.py``.
    """
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan  # type: ignore[assignment]
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original_lifespan
