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


# ---------------------------------------------------------------------------
# Phase 15 Wave 0: MiLB route acceptance scaffold (MILB-06)
# ---------------------------------------------------------------------------


def test_milb_league_slug_accepted() -> None:
    """MILB-06: MiLB league slugs pass the KNOWN_LEAGUES gate once registered.

    This test verifies that 'milb-aaa' (and the other 3 MiLB slugs) are in
    KNOWN_LEAGUES after Phase 15 Wave 1 wires them into LEAGUE_REGISTRY.  The
    KNOWN_LEAGUES gate in the route handler (/{league}/...) rejects unknown slugs
    with 404; a passing KNOWN_LEAGUES check means the request proceeds to the
    resolver/render pipeline.

    Currently (Wave 0): KNOWN_LEAGUES only has 6 ESPN slugs → this assertion
    will FAIL until Wave 1 lands MLBStatsProvider in registry.py.  The test is
    not importorskip-guarded because the registry module itself already exists —
    only its contents need to grow.  Wave 0 deliberately keeps this RED so the
    transition to GREEN is clearly captured in CI.
    """
    from matchup_thumbs.providers.registry import KNOWN_LEAGUES

    _expected_milb_slugs = frozenset({"milb-aaa", "milb-aa", "milb-high-a", "milb-a"})
    missing = _expected_milb_slugs - KNOWN_LEAGUES
    assert not missing, (
        f"MiLB slugs not yet in KNOWN_LEAGUES: {missing}. "
        "Add MLBStatsProvider to LEAGUE_REGISTRY in providers/registry.py "
        "(Phase 15 Wave 1 task)."
    )
