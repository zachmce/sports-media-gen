"""Shared pytest fixtures for matchup-thumbs tests."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from matchup_thumbs.main import app
from matchup_thumbs.settings import Settings


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide a Settings instance with test DSN values via monkeypatched env."""
    monkeypatch.setenv(
        "POSTGRES_DSN",
        "postgresql+psycopg://matchup:matchup@localhost:5432/matchup_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    return Settings()


@pytest.fixture
def client() -> Generator[TestClient]:
    """Yield a TestClient that runs the full app lifespan."""
    with TestClient(app) as c:
        yield c
