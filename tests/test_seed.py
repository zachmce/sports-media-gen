"""Seed job tests (ESPN-01, ESPN-02, ESPN-05).

Covers:
- fetch_teams / select_logo_url unit tests (Task 1)
- generate_aliases, seed upsert idempotency, logo fallback, graceful
  degradation tests (Task 2)
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import psycopg
import pytest
from pytest_httpx import HTTPXMock

from matchup_thumbs.assets import get_placeholder_logo
from matchup_thumbs.espn.client import (
    LEAGUE_ENDPOINTS,
    build_logo_variants,
    derive_variant_key,
    fetch_logo_bytes,
    fetch_teams,
    select_logo_url,
)
from matchup_thumbs.espn.models import ESPNLogo, ESPNTeamEntry
from matchup_thumbs.seed import generate_aliases, normalize_input
from tests.conftest import pg_required

# ---------------------------------------------------------------------------
# Task 1: fetch_teams — pytest-httpx mock against recorded NBA fixture
# ---------------------------------------------------------------------------


async def test_fetch_teams_returns_lakers_first(
    httpx_mock: HTTPXMock,
    espn_nba_fixture: dict[str, Any],
) -> None:
    """fetch_teams (mocked) returns Lakers as the first parsed team (ESPN-01)."""
    league_slug = "nba"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    base_url = "https://site.api.espn.com"
    url = f"{base_url}/apis/site/v2/sports/{path}/teams?limit={limit}"

    httpx_mock.add_response(url=url, json=espn_nba_fixture)

    async with httpx.AsyncClient() as client:
        response = await fetch_teams(client, base_url, league_slug)

    teams = response.sports[0].leagues[0].teams
    assert teams[0].team.slug == "los-angeles-lakers"
    assert teams[0].team.displayName == "Los Angeles Lakers"


async def test_fetch_teams_validates_schema(
    httpx_mock: HTTPXMock,
) -> None:
    """fetch_teams raises ValidationError on malformed ESPN response (ESPN-03)."""
    import pydantic

    league_slug = "nba"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    base_url = "https://site.api.espn.com"
    url = f"{base_url}/apis/site/v2/sports/{path}/teams?limit={limit}"

    # Missing required fields → pydantic.ValidationError
    httpx_mock.add_response(
        url=url,
        json={"sports": [{"leagues": [{"teams": [{"team": {"id": "1"}}]}]}]},
    )

    with pytest.raises(pydantic.ValidationError):
        async with httpx.AsyncClient() as client:
            await fetch_teams(client, base_url, league_slug)


# ---------------------------------------------------------------------------
# Task 1: select_logo_url — unit tests for D-10 fallback chain steps 1-3
# ---------------------------------------------------------------------------


def test_select_logo_url_prefers_default_light() -> None:
    """Step 1: returns the default light logo href when present."""
    logos = [
        ESPNLogo(href="https://espn.com/dark.png", rel=["full", "dark"]),
        ESPNLogo(href="https://espn.com/default.png", rel=["full", "default"]),
    ]
    assert select_logo_url(logos) == "https://espn.com/default.png"


def test_select_logo_url_dark_fallback_when_no_default() -> None:
    """Step 2: returns a dark non-scoreboard logo when no default-light exists."""
    logos = [
        ESPNLogo(
            href="https://espn.com/scoreboard-dark.png",
            rel=["full", "scoreboard", "dark"],
        ),
        ESPNLogo(href="https://espn.com/dark.png", rel=["full", "dark"]),
    ]
    assert select_logo_url(logos) == "https://espn.com/dark.png"


def test_select_logo_url_first_entry_fallback() -> None:
    """Step 3: returns logos[0].href when neither default-light nor dark available."""
    logos = [
        ESPNLogo(href="https://espn.com/scoreboard.png", rel=["full", "scoreboard"]),
        ESPNLogo(
            href="https://espn.com/scoreboard-dark.png",
            rel=["full", "scoreboard", "dark"],
        ),
    ]
    assert select_logo_url(logos) == "https://espn.com/scoreboard.png"


def test_select_logo_url_empty_returns_none() -> None:
    """Empty logos array → None (triggers placeholder fallback in seed)."""
    assert select_logo_url([]) is None


def test_select_logo_url_scoreboard_not_selected_as_default() -> None:
    """Scoreboard logo is never selected as the primary default (step 1 guard)."""
    logos = [
        ESPNLogo(href="https://espn.com/sb.png", rel=["full", "default", "scoreboard"]),
        ESPNLogo(href="https://espn.com/dark.png", rel=["full", "dark"]),
    ]
    # scoreboard is filtered out in step 1; dark is found in step 2
    result = select_logo_url(logos)
    assert result == "https://espn.com/dark.png"


# ---------------------------------------------------------------------------
# Task 1 (08-02): derive_variant_key + build_logo_variants — unit tests
# ---------------------------------------------------------------------------


def test_derive_variant_key() -> None:
    """derive_variant_key maps ESPN rel lists to canonical keys (LOGO-01 / D-03)."""
    # Standard variants
    assert derive_variant_key(["full", "default"]) == "default"
    assert derive_variant_key(["full", "dark"]) == "dark"
    assert derive_variant_key(["full", "scoreboard"]) == "scoreboard"
    # Multi-tag: sorted alphabetically and joined with "_"
    assert derive_variant_key(["full", "scoreboard", "dark"]) == "dark_scoreboard"
    # Edge case: only "full" → empty remainder → "default"
    assert derive_variant_key(["full"]) == "default"
    # Purpose-built color variant (Phase 10 target)
    assert (
        derive_variant_key(["full", "primary_logo_on_primary_color"])
        == "primary_logo_on_primary_color"
    )


def test_build_logo_variants() -> None:
    """build_logo_variants returns all expected keys from the extended fixture."""
    logos = [
        ESPNLogo(
            href="https://a.espncdn.com/i/teamlogos/nba/500/lal.png",
            rel=["full", "default"],
        ),
        ESPNLogo(
            href="https://a.espncdn.com/i/teamlogos/nba/500-dark/lal.png",
            rel=["full", "dark"],
        ),
        ESPNLogo(
            href="https://a.espncdn.com/i/teamlogos/nba/500/primary_on_primary/lal.png",
            rel=["full", "primary_logo_on_primary_color"],
        ),
    ]
    variants = build_logo_variants(logos, "los-angeles-lakers", "nba")

    assert variants["default"] == "https://a.espncdn.com/i/teamlogos/nba/500/lal.png"
    assert (
        variants["dark"] == "https://a.espncdn.com/i/teamlogos/nba/500-dark/lal.png"
    )
    assert variants["primary_logo_on_primary_color"] == (
        "https://a.espncdn.com/i/teamlogos/nba/500/primary_on_primary/lal.png"
    )
    # All three keys are present
    assert set(variants.keys()) == {"default", "dark", "primary_logo_on_primary_color"}

    # Empty logos array → empty dict
    assert build_logo_variants([], "los-angeles-lakers", "nba") == {}


def test_build_logo_variants_collision() -> None:
    """Two logos producing the same key → last-write-wins (D-03).

    The final href in the map must be the second logo's href, and no exception
    should be raised (the collision is logged as a warning, not an error).
    """
    first_logo = ESPNLogo(
        href="https://a.espncdn.com/first.png",
        rel=["full", "default"],
    )
    second_logo = ESPNLogo(
        href="https://a.espncdn.com/second.png",
        rel=["full", "default"],
    )
    variants = build_logo_variants(
        [first_logo, second_logo], "test-team", "test-league"
    )

    # Last-write-wins: second logo's href overwrites first
    assert variants["default"] == "https://a.espncdn.com/second.png"
    # Only one key in the map (no duplicate keys)
    assert list(variants.keys()) == ["default"]


# ---------------------------------------------------------------------------
# Task 1: fetch_logo_bytes — tenacity decorator validation
# ---------------------------------------------------------------------------


def test_fetch_logo_bytes_has_tenacity_retry() -> None:
    """fetch_logo_bytes carries the tenacity retry decorator (stop_after_attempt(3))."""
    import tenacity

    assert hasattr(fetch_logo_bytes, "retry")
    retry_obj = fetch_logo_bytes.retry  # type: ignore[attr-defined]
    assert isinstance(retry_obj, (tenacity.Retrying, tenacity.AsyncRetrying))


# ---------------------------------------------------------------------------
# Task 2: normalize_input — unit test
# ---------------------------------------------------------------------------


def test_normalize_input_casefolds_and_strips() -> None:
    """normalize_input casefolds and removes non-alphanumerics."""
    assert normalize_input("LA-Lakers") == "lalakers"
    assert normalize_input("lakerz") == "lakerz"
    assert normalize_input("LAL") == "lal"
    assert normalize_input("Los Angeles Lakers") == "losangeleslakers"
    assert normalize_input("") == ""


# ---------------------------------------------------------------------------
# Task 2: generate_aliases — alias generation + nickname skip
# ---------------------------------------------------------------------------


def _make_lakers_entry() -> ESPNTeamEntry:
    """Return a Lakers-like ESPNTeamEntry (nickname matches location)."""
    return ESPNTeamEntry(
        id="13",
        slug="los-angeles-lakers",
        abbreviation="LAL",
        displayName="Los Angeles Lakers",
        shortDisplayName="Lakers",
        name="Lakers",
        location="Los Angeles",
        # nickname is NOT a field on ESPNTeamEntry — it is deliberately excluded
    )


def test_alias_generation_skips_nickname() -> None:
    """ESPN 'nickname' duplicates 'location' and must NOT produce a double alias.

    generate_aliases draws from: slug, abbreviation, location, name,
    displayName, shortDisplayName.  It explicitly SKIPS the 'nickname' field
    (which equals location for all major-league teams) so no duplicate alias
    is emitted for the location value.
    """
    team = _make_lakers_entry()
    aliases = generate_aliases(team)

    # Must include expected aliases
    assert "lakers" in aliases  # from name / shortDisplayName
    assert "lal" in aliases  # from abbreviation
    assert "losangeles" in aliases  # from location
    assert "losangeleslakers" in aliases  # from displayName / slug

    # Must be deduplicated
    assert len(aliases) == len(set(aliases)), "Aliases must be de-duplicated"

    # The NBA/NFL/MLB/NHL 'nickname' field equals location; verify no double-count
    # of the location alias beyond the one entry from the location field itself.
    location_norm = normalize_input("Los Angeles")
    assert aliases.count(location_norm) == 1, (
        "location alias must appear exactly once (nickname skipped)"
    )


# ---------------------------------------------------------------------------
# Task 2: test_logo_fallback — empty logos → placeholder bytes (ESPN-02)
# ---------------------------------------------------------------------------


def test_logo_fallback() -> None:
    """ESPN-02: placeholder bytes are non-empty valid PNG bytes.

    The D-10 fallback chain terminates at get_placeholder_logo() when a team
    has no usable logos.  This test validates the placeholder itself is a
    well-formed PNG so the fallback path never returns corrupt bytes.

    Note: _pick_logo_bytes_sync was removed (WR-06) — it always returned the
    placeholder regardless of logos, making it indistinguishable from calling
    get_placeholder_logo() directly.  The async fallback chain lives in
    _resolve_logo_bytes() inside seed.run().
    """
    placeholder = get_placeholder_logo()
    assert placeholder, "Placeholder bytes must be non-empty"
    assert placeholder[:4] == b"\x89PNG", "Placeholder must be valid PNG bytes"


# ---------------------------------------------------------------------------
# Task 2: test_seed_upsert_idempotent — pg-guarded (ESPN-01 / D-03)
# ---------------------------------------------------------------------------


@pg_required
async def test_seed_upsert_idempotent(espn_nba_fixture: dict[str, Any]) -> None:
    """ESPN-01 / D-03: running the seed twice does not duplicate rows.

    Seeds the recorded NBA fixture twice into a real test DB via mocked httpx
    and verifies team/alias counts remain identical after the second run.
    """
    import os

    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis

    from matchup_thumbs.seed import run as seed_run

    postgres_dsn = os.environ.get("POSTGRES_DSN", "")
    raw_dsn = postgres_dsn.replace("postgresql+psycopg://", "postgresql://")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    league_slug = "nba"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    base_url = "https://site.api.espn.com"
    espn_url = f"{base_url}/apis/site/v2/sports/{path}/teams?limit={limit}"

    conninfo = raw_dsn

    # Seed with two fresh teams from the fixture.  Run twice; counts must be stable.
    try:
        async with AsyncConnectionPool(
            conninfo=conninfo, min_size=1, max_size=2
        ) as pool:
            redis_client: Redis = Redis.from_url(redis_url, decode_responses=False)
            try:
                transport = httpx.MockTransport(
                    handler=_make_espn_mock_handler(espn_url, espn_nba_fixture)
                )
                async with httpx.AsyncClient(transport=transport) as http_client:
                    # First run
                    await seed_run(pool, redis_client, http_client, [league_slug])
                    counts_1 = await _count_nba_rows(pool)

                transport2 = httpx.MockTransport(
                    handler=_make_espn_mock_handler(espn_url, espn_nba_fixture)
                )
                # Second run (idempotent)
                async with httpx.AsyncClient(transport=transport2) as http_client2:
                    await seed_run(pool, redis_client, http_client2, [league_slug])
                counts_2 = await _count_nba_rows(pool)

                assert counts_1 == counts_2, (
                    f"Row counts changed after second seed run: {counts_1} → {counts_2}"
                )
            finally:
                await redis_client.aclose()
    finally:
        # Cleanup seeded teams
        with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM team_aliases
                WHERE team_id IN (
                    SELECT t.id FROM teams t
                    JOIN leagues l ON l.id = t.league_id
                    WHERE l.slug = 'nba'
                      AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
                )
                """
            )
            cur.execute(
                """
                DELETE FROM teams t
                USING leagues l
                WHERE l.id = t.league_id
                  AND l.slug = 'nba'
                  AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
                """
            )
        # psycopg commit happens on context manager exit (no explicit commit needed)


# ---------------------------------------------------------------------------
# Task 3 (08-03): test_seed_upsert_preserves_logo_variants — pg-guarded (LOGO-02)
# ---------------------------------------------------------------------------


@pg_required
async def test_seed_upsert_preserves_logo_variants(
    espn_nba_fixture: dict[str, Any],
) -> None:
    """LOGO-02: seed persists logo_variants and re-running seed is idempotent.

    Drives the real seed path via mocked httpx against the extended NBA fixture,
    then verifies with a sync psycopg connection:
    (1) The Lakers' logo_variants is a non-empty dict containing expected keys.
    (2) Re-running seed leaves exactly one row for the Lakers (no duplicate).
    (3) The logo_variants value is preserved/replaced (no orphaned partial data).
    """
    import os

    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis

    from matchup_thumbs.seed import run as seed_run

    postgres_dsn = os.environ.get("POSTGRES_DSN", "")
    raw_dsn = postgres_dsn.replace("postgresql+psycopg://", "postgresql://")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    league_slug = "nba"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    base_url = "https://site.api.espn.com"
    espn_url = f"{base_url}/apis/site/v2/sports/{path}/teams?limit={limit}"

    conninfo = raw_dsn
    lakers_slug = "los-angeles-lakers"

    try:
        async with AsyncConnectionPool(
            conninfo=conninfo, min_size=1, max_size=2
        ) as pool:
            redis_client: Redis = Redis.from_url(redis_url, decode_responses=False)
            try:
                # First seed run
                transport1 = httpx.MockTransport(
                    handler=_make_espn_mock_handler(espn_url, espn_nba_fixture)
                )
                async with httpx.AsyncClient(transport=transport1) as http_client:
                    await seed_run(pool, redis_client, http_client, [league_slug])

                # (1) Assert logo_variants is a non-empty dict with expected keys
                with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT logo_variants, COUNT(*) AS row_count
                        FROM teams t
                        JOIN leagues l ON l.id = t.league_id
                        WHERE l.slug = %(league)s
                          AND t.slug = %(slug)s
                        GROUP BY logo_variants
                        """,
                        {"league": league_slug, "slug": lakers_slug},
                    )
                    rows = cur.fetchall()

                assert len(rows) == 1, (
                    f"Expected exactly 1 distinct logo_variants row for Lakers "
                    f"after first seed, got {len(rows)}"
                )
                variants_after_first: Any = rows[0][0]
                assert isinstance(variants_after_first, dict), (
                    f"logo_variants must be a dict (psycopg3 auto-deserializes jsonb), "
                    f"got {type(variants_after_first)}"
                )
                assert len(variants_after_first) > 0, (
                    "logo_variants must be non-empty for Lakers (fixture has 5 logos)"
                )
                # Extended fixture includes: default, dark, scoreboard,
                # primary_logo_on_primary_color, primary_logo_on_white_color
                assert "default" in variants_after_first, (
                    "logo_variants must contain 'default' key"
                )
                assert "primary_logo_on_primary_color" in variants_after_first, (
                    "logo_variants must contain 'primary_logo_on_primary_color' key "
                    "(Phase 10 target variant)"
                )

                # Second seed run (idempotent)
                transport2 = httpx.MockTransport(
                    handler=_make_espn_mock_handler(espn_url, espn_nba_fixture)
                )
                async with httpx.AsyncClient(transport=transport2) as http_client2:
                    await seed_run(pool, redis_client, http_client2, [league_slug])

                # (2) Assert no duplicate rows after re-seed
                with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM teams t
                        JOIN leagues l ON l.id = t.league_id
                        WHERE l.slug = %(league)s
                          AND t.slug = %(slug)s
                        """,
                        {"league": league_slug, "slug": lakers_slug},
                    )
                    count_row = cur.fetchone()

                assert count_row is not None
                row_count: int = count_row[0]
                assert row_count == 1, (
                    f"Re-seed must not duplicate rows: expected 1, got {row_count}"
                )

                # (3) Assert logo_variants value is preserved/replaced after re-seed
                with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT logo_variants FROM teams t
                        JOIN leagues l ON l.id = t.league_id
                        WHERE l.slug = %(league)s
                          AND t.slug = %(slug)s
                        LIMIT 1
                        """,
                        {"league": league_slug, "slug": lakers_slug},
                    )
                    variant_row = cur.fetchone()

                assert variant_row is not None
                variants_after_second: Any = variant_row[0]
                assert isinstance(variants_after_second, dict), (
                    "logo_variants must still be a dict after re-seed"
                )
                assert variants_after_second == variants_after_first, (
                    "logo_variants must be identical after re-seed (idempotent upsert)"
                )

            finally:
                await redis_client.aclose()
    finally:
        # Cleanup: remove seeded test rows
        with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM team_aliases
                WHERE team_id IN (
                    SELECT t.id FROM teams t
                    JOIN leagues l ON l.id = t.league_id
                    WHERE l.slug = 'nba'
                      AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
                )
                """
            )
            cur.execute(
                """
                DELETE FROM teams t
                USING leagues l
                WHERE l.id = t.league_id
                  AND l.slug = 'nba'
                  AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
                """
            )


# ---------------------------------------------------------------------------
# Task 2: test_seed_degrade_no_truncate — pg-guarded (ESPN-05 / D-15)
# ---------------------------------------------------------------------------


@pg_required
async def test_seed_degrade_no_truncate(
    seeded_registry: None,
) -> None:
    """ESPN-05: when ESPN is unreachable, existing rows are preserved.

    Pre-seeds data via seeded_registry fixture, then mocks ESPN returning 503
    and verifies the seed exits non-zero (exception raised) but does NOT delete
    or truncate any previously seeded team rows.
    """
    import os

    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis

    from matchup_thumbs.seed import run as seed_run

    postgres_dsn = os.environ.get("POSTGRES_DSN", "")
    raw_dsn = postgres_dsn.replace("postgresql+psycopg://", "postgresql://")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Verify pre-existing rows (from seeded_registry)
    with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM teams t
            JOIN leagues l ON l.id = t.league_id
            WHERE l.slug = 'nba'
              AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
            """
        )
        pre_row = cur.fetchone()
        assert pre_row is not None
        pre_count: int = pre_row[0]
    assert pre_count > 0, "seeded_registry must have inserted teams before this test"

    conninfo = raw_dsn

    # Mock ESPN to return 503 for every request
    def espn_503_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"Service Unavailable")

    async with AsyncConnectionPool(conninfo=conninfo, min_size=1, max_size=2) as pool:
        redis_client: Redis = Redis.from_url(redis_url, decode_responses=False)
        try:
            transport = httpx.MockTransport(handler=espn_503_handler)
            async with httpx.AsyncClient(transport=transport) as http_client:
                with pytest.raises(httpx.HTTPStatusError):
                    await seed_run(pool, redis_client, http_client, ["nba"])
        finally:
            await redis_client.aclose()

    # Verify existing rows were NOT touched
    with psycopg.connect(raw_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM teams t
            JOIN leagues l ON l.id = t.league_id
            WHERE l.slug = 'nba'
              AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
            """
        )
        post_row = cur.fetchone()
        assert post_row is not None
        post_count: int = post_row[0]
    assert post_count == pre_count, (
        f"ESPN failure must not mutate existing rows: {pre_count} → {post_count}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_espn_mock_handler(
    expected_url: str,
    fixture: dict[str, Any],
) -> Any:
    """Return an httpx mock transport handler that responds with the fixture JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == expected_url:
            return httpx.Response(
                200,
                content=json.dumps(fixture).encode(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    return handler


async def _count_nba_rows(pool: Any) -> tuple[int, int]:
    """Return (team_count, alias_count) for NBA teams from the fixture."""
    from psycopg import rows as pg_rows

    async with pool.connection() as conn:
        conn.row_factory = pg_rows.tuple_row
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(*) FROM teams t
                JOIN leagues l ON l.id = t.league_id
                WHERE l.slug = 'nba'
                  AND t.slug IN ('los-angeles-lakers', 'los-angeles-clippers')
                """
            )
            team_row = await cur.fetchone()
            team_count: int = team_row[0] if team_row else 0

            await cur.execute(
                """
                SELECT COUNT(*) FROM team_aliases ta
                JOIN leagues l ON l.id = ta.league_id
                WHERE l.slug = 'nba'
                """
            )
            alias_row = await cur.fetchone()
            alias_count: int = alias_row[0] if alias_row else 0

    return team_count, alias_count
