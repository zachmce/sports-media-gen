"""Wave 0 scaffolds for image route tests (API-01, API-02).

These placeholders collect under pytest and are skipped so the suite
stays green while downstream wave 04-02 implements real assertions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_image_route_200(client: TestClient) -> None:
    """GET /{league}/{away}/{home}/{kind} returns 200 PNG (API-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_image_route_cache_control_immutable(client: TestClient) -> None:
    """Image response carries Cache-Control: public, max-age=2592000, immutable."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_unknown_away_team_404(client: TestClient) -> None:
    """Unresolvable away team returns 404 with structured D-07 body (API-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_unknown_home_team_404(client: TestClient) -> None:
    """Unresolvable home team returns 404 with structured D-07 body (API-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_unknown_kind_400(client: TestClient) -> None:
    """Unknown kind → UnknownGeneratorError → 400 unknown_generator (API-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_webp_fmt(client: TestClient) -> None:
    """?fmt=webp returns WebP response with correct content-type (API-01, OUT-01)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_width_clamp_route(client: TestClient) -> None:
    """?w=N returns image with clamped width (API-01, OUT-02)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_ncaa_sport_mapping(client: TestClient) -> None:
    """GET /ncaa/football/... maps to ncaaf league slug (API-02)."""


@pytest.mark.skip(reason="Wave 0 scaffold — implemented in 04-02")
def test_unknown_ncaa_sport_404(client: TestClient) -> None:
    """Unknown NCAA sport returns 404 with unknown_sport error (API-02)."""
