"""Resolver tests (RES-01 through RES-06).

Wave 0 stubs — full implementations arrive in Plan 02-03.
Stub tests skip so the suite stays green while providing the anchor
test function names that VALIDATION.md -k selectors reference.

Integration tests (test_resolver_exact, test_resolver_fuzzy, test_resolver_scope)
are guarded by pg_required and will run when POSTGRES_DSN is set.
Unit tests (test_resolver_404, test_resolver_cache) use mock_pool/mock_redis
and do not require live services.
"""

import pytest

from tests.conftest import pg_required


@pg_required
def test_resolver_exact(seeded_registry: None) -> None:
    """RES-01: exact alias match resolves to the correct team.

    'lakers' resolves to Los Angeles Lakers (NBA) at Stage 1 (exact match).
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-03")


@pg_required
def test_resolver_fuzzy(seeded_registry: None) -> None:
    """RES-03: fuzzy match above 0.5 threshold resolves to the correct team.

    'lakerz' (typo) resolves to Los Angeles Lakers via trigram similarity ~0.556.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-03")


@pg_required
def test_resolver_scope(seeded_registry: None) -> None:
    """RES-04: resolution is league-scoped and never crosses leagues.

    'lac' in NBA resolves to Clippers; 'lac' in NFL resolves to Chargers.
    The same alias in two leagues must resolve to different teams.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-03")


def test_resolver_404() -> None:
    """RES-06: unresolvable input returns None from the resolver function.

    The route layer converts None to HTTP 404 with a JSON body containing
    'league' and 'input' fields.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-03")


def test_resolver_cache() -> None:
    """RES-05: positive resolution is cached in Redis with 7-day TTL.

    Verifies that after a successful resolution the resolver writes the
    team_id to Redis at key resolve:{league}:{normalized_input} with
    ex=settings.resolve_positive_ttl (604800 seconds).
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-03")
