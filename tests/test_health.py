"""Wave 0 scaffolds for /readyz health route tests (API-06).

These placeholders collect under pytest and are skipped so the suite
stays green while downstream wave 04-02 implements real assertions.

Note: /healthz tests already live in test_app.py (API-05 — already passing).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_readyz_200(client: TestClient) -> None:
    """GET /readyz returns 200 {"status": "ready"} when both deps are up (API-06)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_readyz_503_postgres_down(client: TestClient) -> None:
    """GET /readyz returns 503 with postgres=False when Postgres is down (API-06)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_readyz_503_redis_down(client: TestClient) -> None:
    """GET /readyz returns 503 with redis=False when Redis is down (API-06)."""
