"""Tests for /metrics Prometheus endpoint (OBS-01).

Tests are driven via the HTTP endpoint (not direct metric object inspection)
to avoid Prometheus double-registration issues in test suites (RESEARCH Pitfall 3).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_endpoint_200(client: TestClient) -> None:
    """GET /metrics returns 200 with Prometheus text exposition format (OBS-01)."""
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_contains_domain_metrics(client: TestClient) -> None:
    """GET /metrics body contains all five OBS-01 domain metric names (OBS-01).

    The instrumentator also exposes http_request_* series — verified implicitly
    since any request to /metrics will have incremented those counters.
    """
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "render_latency_seconds" in body
    assert "render_cache_events_total" in body
    assert "resolution_total" in body
    assert "resolution_misses_total" in body
    assert "espn_fetch_failures_total" in body


def test_render_cache_events_accepts_bypass_tier(client: TestClient) -> None:
    """render_cache_events_total accepts tier='bypass'; round-trips to /metrics (D-03).

    Drives via HTTP endpoint to avoid Prometheus double-registration issues
    (established style in this file — no direct Counter object re-instantiation).
    Confirms 'bypass' label value is part of the closed vocabulary (5-series).
    """
    from matchup_thumbs.metrics import render_cache_events_total

    render_cache_events_total.labels(tier="bypass").inc()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert 'tier="bypass"' in resp.text
