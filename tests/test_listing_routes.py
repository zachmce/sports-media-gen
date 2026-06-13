"""Listing route tests — GET /leagues and GET /{league}/teams (API-03, API-04).

Wave 0 stubs — full implementations arrive in Plan 02-04.
Stub tests skip so the suite stays green while providing the anchor
test function names that VALIDATION.md -k selectors reference.

These tests use the TestClient with the stub lifespan (no live DB required
for the stubs; the full implementations will inject a mock pool that returns
seeded fixture rows).
"""

import pytest


def test_listing_leagues() -> None:
    """API-04: GET /leagues returns a list of league objects.

    Each item must have slug, display_name, and sport fields.
    The six expected league slugs (nba, nfl, mlb, nhl, ncaaf, ncaab) must
    all appear in the response.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-04")


def test_listing_teams() -> None:
    """API-03: GET /{league}/teams returns slug, display_name, abbreviation, aliases.

    For a known league (e.g. 'nba'), returns a list of team objects each with
    a non-empty aliases list.  An unknown league slug returns HTTP 404 with a
    JSON body containing 'league' and 'error' keys.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-04")
