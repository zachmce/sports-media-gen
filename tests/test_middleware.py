"""Wave 0 scaffolds for request-logging middleware tests (OBS-02).

These placeholders collect under pytest and are skipped so the suite
stays green while downstream wave 04-03 implements real assertions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_request_completed_log_fields(client: TestClient) -> None:
    """Middleware emits request_completed with request_id, latency_ms (OBS-02)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_x_request_id_honored(client: TestClient) -> None:
    """Inbound X-Request-ID is honored and echoed on the response (OBS-02, D-12)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_contextvars_no_bleed_after_image_request(client: TestClient) -> None:
    """Structlog contextvars from an image request do not bleed into a subsequent
    probe request in the same asyncio task (OBS-02, Pitfall 2).
    """


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-03")
def test_404_path_no_stale_cache_tier(client: TestClient) -> None:
    """A 404 probe response does not carry a stale cache_tier from a prior image
    request — confirms clear_contextvars() at middleware entry (OBS-02).
    """
