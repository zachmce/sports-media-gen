"""Request-logging middleware tests (OBS-02).

Most tests are Wave 0 scaffolds (skipped until 04-03 wires the middleware).
The exception is ``test_404_path_no_stale_cache_tier``, which verifies that
a 404 image request carries no stale ``cache_tier`` from a prior successful
request.  This test is also skipped until 04-03 activates the middleware that
calls ``clear_contextvars()`` at request start (Pitfall 2 guard, D-13).

Once 04-03 lands, 04-03 Task 2 removes the ``skip`` markers from this file.
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


@pytest.mark.skip(
    reason="activated in 04-03 once middleware clears contextvars at request start"
)
def test_404_path_no_stale_cache_tier(client: TestClient) -> None:
    """A 404 image request carries no stale cache_tier from a prior successful request.

    Scenario (requires RequestLoggingMiddleware from 04-03):
    1. Drive a successful image request → route binds cache_tier to contextvars.
    2. Drive a 404 image request (away resolve returns None) → route never binds
       cache_tier (D-05 step 3 exits before step 5).
    3. Assert the request_completed log line for the 404 request has no cache_tier
       key — confirming clear_contextvars() at middleware entry prevented bleed.

    When 04-03 lands, flip this skip to an active test using:
        import io
        from unittest.mock import AsyncMock, patch
        import structlog.testing
        from PIL import Image
        from matchup_thumbs.render import RenderResult

    Assertion: the request_completed event for the 404 request must not have
    a 'cache_tier' key (or it must be absent/None), never the prior tier value.
    """
