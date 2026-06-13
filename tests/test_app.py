"""Tests for the FastAPI app, health route, and Pillow WebP support."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import features

from matchup_thumbs.main import app


def test_app_imports() -> None:
    """The FastAPI app object should be a FastAPI instance."""
    assert isinstance(app, FastAPI)


def test_healthz(client: TestClient) -> None:
    """GET /healthz returns 200 with {"status": "ok"}."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_pillow_webp() -> None:
    """Pillow should be built with WebP module support available."""
    assert features.check_module("webp"), "Pillow WebP module is not available"
