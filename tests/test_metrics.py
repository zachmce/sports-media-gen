"""Wave 0 scaffolds for /metrics endpoint tests (OBS-01).

These placeholders collect under pytest and are skipped so the suite
stays green while downstream wave 04-03 implements real assertions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_metrics_endpoint_200(client: TestClient) -> None:
    """GET /metrics returns 200 with Prometheus text exposition format (OBS-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_metrics_contains_domain_metrics(client: TestClient) -> None:
    """GET /metrics body contains all four OBS-01 domain metric names (OBS-01)."""
