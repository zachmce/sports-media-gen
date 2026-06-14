"""Request-logging middleware tests (OBS-02).

All tests are now active: 04-03 wires RequestLoggingMiddleware into main.py,
enabling the contextvar-bleed guard and the 404 stale-tier guard.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, patch

import structlog.contextvars
import structlog.testing
from fastapi.testclient import TestClient
from PIL import Image

from matchup_thumbs.middleware import _REQUEST_ID_MAX_LEN, _sanitize_request_id
from matchup_thumbs.render import RenderResult

# Shorthand: capture_logs with merge_contextvars so bound context vars are
# included in each captured event dict (capture_logs() alone disables the
# processor chain, so merge_contextvars must be passed explicitly).
_capture_logs = structlog.testing.capture_logs


def _cap():
    """Return a capture_logs context manager that merges structlog contextvars."""
    return _capture_logs(processors=[structlog.contextvars.merge_contextvars])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(w: int = 1280, h: int = 720) -> bytes:
    """Build a minimal real PNG so post_cache_transform has something to decode."""
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (85, 37, 131)).save(buf, format="PNG")
    return buf.getvalue()


def _team(slug: str) -> dict[str, Any]:
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
        "espn_id": "13",
    }


# ---------------------------------------------------------------------------
# OBS-02: Per-request structured log fields
# ---------------------------------------------------------------------------


def test_request_completed_log_fields(client: TestClient) -> None:
    """Middleware emits request_completed with request_id, method, path,
    status_code, and latency_ms on every request (OBS-02).
    """
    with _cap() as cap:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    completed = [e for e in cap if e.get("event") == "request_completed"]
    assert len(completed) == 1
    log = completed[0]
    assert "request_id" in log
    assert log["method"] == "GET"
    assert log["path"] == "/healthz"
    assert log["status_code"] == 200
    assert "latency_ms" in log
    assert isinstance(log["latency_ms"], float)


def test_x_request_id_honored(client: TestClient) -> None:
    """Inbound X-Request-ID is honored and echoed on the response (OBS-02, D-12).

    When the header is absent, a generated id is present (non-empty hex string).
    """
    # With explicit X-Request-ID
    resp = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "abc123"

    # Without X-Request-ID — a uuid4().hex is generated and echoed
    resp2 = client.get("/healthz")
    assert resp2.status_code == 200
    generated = resp2.headers.get("x-request-id")
    assert generated is not None
    assert len(generated) == 32  # uuid4().hex is always 32 hex chars


def test_sanitize_request_id_strips_control_chars_and_bounds_length() -> None:
    """Inbound X-Request-ID is reduced to [A-Za-z0-9._-] and length-bounded (WR-02).

    A forged value carrying CR/LF (log-injection attempt) has the control
    characters and other disallowed bytes stripped; an over-long value is
    truncated; a value that sanitizes to empty (or an absent header) falls back
    to a fresh uuid4().hex.
    """
    # CR/LF + '=' + space are all disallowed and removed.
    cleaned = _sanitize_request_id("real\r\nfake_event=injected value")
    assert "\r" not in cleaned and "\n" not in cleaned and " " not in cleaned
    assert cleaned == "realfake_eventinjectedvalue"

    # Over-long value is truncated to the bound.
    assert len(_sanitize_request_id("a" * 500)) == _REQUEST_ID_MAX_LEN

    # All-disallowed input falls back to a generated 32-char hex id.
    assert len(_sanitize_request_id("\r\n\t   ")) == 32
    # Absent header falls back to a generated id too.
    assert len(_sanitize_request_id(None)) == 32


def test_contextvars_no_bleed_after_image_request(client: TestClient) -> None:
    """Structlog contextvars from an image request do not bleed into a subsequent
    probe request in the same asyncio task (OBS-02, Pitfall 2 guard).

    Scenario:
    1. Drive a SUCCESSFUL image request → route binds league/kind/cache_tier.
    2. Drive GET /healthz → route binds nothing.
    3. Assert the request_completed for /healthz has NO league, kind, or cache_tier
       key — confirming clear_contextvars() at middleware entry erased the prior
       request's context before the new one started.
    """
    hit_result = RenderResult(png=_make_png_bytes(), tier="hit")
    away = _team("lakers")
    home = _team("celtics")

    with (
        patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(side_effect=[away, home]),
        ),
        patch(
            "matchup_thumbs.routes.images.render_pipeline",
            new=AsyncMock(return_value=hit_result),
        ),
        _cap() as cap,
    ):
        # Step 1: successful image request — binds league/kind/cache_tier
        image_resp = client.get("/nba/lakers/celtics/thumb")
        # Step 2: probe request — must NOT inherit the image request's context
        probe_resp = client.get("/healthz")

    assert image_resp.status_code == 200
    assert probe_resp.status_code == 200

    # Find the request_completed events by path
    image_completed = [
        e
        for e in cap
        if e.get("event") == "request_completed"
        and e.get("path") == "/nba/lakers/celtics/thumb"
    ]
    probe_completed = [
        e
        for e in cap
        if e.get("event") == "request_completed" and e.get("path") == "/healthz"
    ]

    assert len(image_completed) == 1, "Expected 1 request_completed for image route"
    assert len(probe_completed) == 1, "Expected 1 request_completed for /healthz"

    # The image request should have cache_tier (bound after successful render)
    assert "cache_tier" in image_completed[0]

    # The /healthz request_completed must have NO league, kind, or cache_tier
    probe_log = probe_completed[0]
    assert "league" not in probe_log, f"Bleed: 'league' in /healthz log: {probe_log}"
    assert "kind" not in probe_log, f"Bleed: 'kind' in /healthz log: {probe_log}"
    assert "cache_tier" not in probe_log, (
        f"Bleed: 'cache_tier' in /healthz log: {probe_log}"
    )


def test_404_path_no_stale_cache_tier(client: TestClient) -> None:
    """A 404 image request carries no stale cache_tier from a prior successful request.

    Scenario (Pitfall 2 error-path variant):
    1. Drive a successful image request → binds cache_tier=hit to contextvars.
    2. Drive a 404 image request (away resolve returns None) → route never calls
       bind_contextvars(cache_tier=...) because it 404s before the render step.
    3. Assert the request_completed for the 404 request has no cache_tier key
       (or it is absent) — never the prior "hit" value.

    This test activates the 04-03 middleware: clear_contextvars() at request
    start erases "hit" from step 1 before step 2 binds anything.
    """
    hit_result = RenderResult(png=_make_png_bytes(), tier="hit")
    away = _team("lakers")
    home = _team("celtics")

    with _cap() as cap:
        # Step 1: successful image request
        with (
            patch(
                "matchup_thumbs.routes.images.resolve",
                new=AsyncMock(side_effect=[away, home]),
            ),
            patch(
                "matchup_thumbs.routes.images.render_pipeline",
                new=AsyncMock(return_value=hit_result),
            ),
        ):
            ok_resp = client.get("/nba/lakers/celtics/thumb")

        # Step 2: 404 image request (away team not found)
        with patch(
            "matchup_thumbs.routes.images.resolve",
            new=AsyncMock(return_value=None),
        ):
            not_found_resp = client.get("/nba/zzz/celtics/thumb")

    assert ok_resp.status_code == 200
    assert not_found_resp.status_code == 404

    # Find the 404 request's request_completed log entry
    not_found_completed = [
        e
        for e in cap
        if e.get("event") == "request_completed"
        and e.get("path") == "/nba/zzz/celtics/thumb"
    ]
    assert len(not_found_completed) == 1, "Expected 1 request_completed for 404"

    not_found_log = not_found_completed[0]
    assert not_found_log["status_code"] == 404
    # cache_tier must be absent — not "hit" from the prior request
    assert "cache_tier" not in not_found_log, (
        f"Stale cache_tier in 404 log: {not_found_log}"
    )
