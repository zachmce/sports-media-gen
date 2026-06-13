"""Seed job tests (ESPN-01, ESPN-02, ESPN-05).

Wave 0 stubs — full implementations arrive in Plan 02-02.
Stub tests skip so the suite stays green while providing the anchor
test function names that VALIDATION.md -k selectors reference.
"""

import pytest

from tests.conftest import pg_required


@pg_required
def test_alias_generation_skips_nickname() -> None:
    """ESPN 'nickname' duplicates 'location' and must be skipped for alias seeding.

    Validates that the seed alias generator does NOT produce a 'nickname'
    alias (which always equals location for NBA/NFL/MLB/NHL teams) to avoid
    spurious duplicate aliases that hit ON CONFLICT DO NOTHING.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-02")


def test_logo_fallback() -> None:
    """ESPN-02: when ESPN returns no usable logo, placeholder bytes are returned.

    The D-10 fallback chain: primary logo → dark variant → placeholder PNG.
    Verifies that get_placeholder_logo() is invoked when logos=[] and that
    the returned bytes are valid PNG.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-02")


@pg_required
def test_seed_upsert_idempotent() -> None:
    """ESPN-01 / D-03: running the seed twice does not duplicate rows.

    Calls the seed upsert logic with the recorded ESPN NBA fixture twice and
    verifies the team count remains identical after the second run.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-02")


@pg_required
def test_seed_degrade_no_truncate() -> None:
    """ESPN-05: when ESPN is unreachable, existing rows are preserved.

    Mocks ESPN returning 503 and verifies the seed exits non-zero but does
    not delete or truncate any previously seeded team rows.
    """
    pytest.skip("Wave 0 stub — implemented in Plan 02-02")
