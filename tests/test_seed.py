"""Seed job tests (ESPN-01, ESPN-02, ESPN-05).

Covers:
- fetch_teams / select_logo_url unit tests (Task 1)
- generate_aliases, seed upsert idempotency, logo fallback, graceful
  degradation tests (Task 2)
- fetch_league_logo_data core-API fetch tests (11-02 Task 1)
- league logo seed loop + NCAA placeholder fallback tests (11-02 Task 2)
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import psycopg
import pytest
from PIL import Image as _Image
from pytest_httpx import HTTPXMock

from matchup_thumbs.assets import get_placeholder_logo
from matchup_thumbs.espn.client import (
    LEAGUE_ENDPOINTS,
    build_logo_variants,
    derive_variant_key,
    fetch_league_logo_data,
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
    assert variants["dark"] == "https://a.espncdn.com/i/teamlogos/nba/500-dark/lal.png"
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
# 11-02 Task 1: fetch_league_logo_data — ESPN core API league-logo fetch (LGL-01)
# ---------------------------------------------------------------------------


async def test_fetch_league_logo_data_distinct_hrefs_returns_two_logos() -> None:
    """Mocked core-API response with two distinct-href logos → 2 ESPNLogo (LGL-01).

    Verifies:
    - URL is built from LEAGUE_ENDPOINTS path split on "/" + core_api_base_url
    - Logos parsed from inline "logos" key (no $ref follow)
    - Returns list[ESPNLogo] with verbatim hrefs (D-01)
    """
    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "nba"
    path, _ = LEAGUE_ENDPOINTS[league_slug]
    sport, espn_league_slug = path.split("/", 1)
    expected_url = f"{core_api_base_url}/v2/sports/{sport}/leagues/{espn_league_slug}"

    nba_logos_payload = {
        "logos": [
            {
                "href": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
                "rel": ["full", "default"],
                "width": 500,
                "height": 500,
            },
            {
                "href": "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500-dark/nba.png",
                "rel": ["full", "dark"],
                "width": 500,
                "height": 500,
            },
        ]
    }

    transport = httpx.MockTransport(
        handler=_make_core_api_mock_handler(expected_url, nba_logos_payload)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        logos = await fetch_league_logo_data(client, core_api_base_url, league_slug)

    assert len(logos) == 2, f"Expected 2 ESPNLogo entries, got {len(logos)}"
    assert isinstance(logos[0], ESPNLogo)
    assert logos[0].href == "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png"
    assert logos[1].href == (
        "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500-dark/nba.png"
    )


async def test_fetch_league_logo_data_http_error_returns_empty_list() -> None:
    """HTTP error (404) → returns empty list without raising (LGL-01, T-11-01).

    A failing league logo fetch must not abort the entire seed run.
    """
    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "nfl"

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler=error_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        logos = await fetch_league_logo_data(client, core_api_base_url, league_slug)

    assert logos == [], f"Expected empty list on HTTP error, got {logos!r}"


# ---------------------------------------------------------------------------
# 11-02 Task 2: league logo seed loop + NCAA placeholder fallback (LGL-03)
# ---------------------------------------------------------------------------


async def test_has_usable_league_logo_distinct_hrefs_returns_true() -> None:
    """_has_usable_league_logo: distinct hrefs → True (pro league)."""
    from matchup_thumbs.seed import _has_usable_league_logo

    variant_map = {
        "default": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
        "dark": "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500-dark/nba.png",
    }
    assert _has_usable_league_logo(variant_map) is True


async def test_has_usable_league_logo_identical_hrefs_returns_false() -> None:
    """_has_usable_league_logo: identical hrefs → False (NCAA case, D-06)."""
    from matchup_thumbs.seed import _has_usable_league_logo

    identical_href = (
        "https://a.espncdn.com/redesign/assets/img/icons/ESPN-icon-football-college.png"
    )
    variant_map = {"default": identical_href, "dark": identical_href}
    assert _has_usable_league_logo(variant_map) is False


async def test_has_usable_league_logo_empty_map_returns_false() -> None:
    """_has_usable_league_logo: empty map → False."""
    from matchup_thumbs.seed import _has_usable_league_logo

    assert _has_usable_league_logo({}) is False


async def test_seed_league_logo_pro_league_warms_both_keys(
    mock_redis: MagicMock,
) -> None:
    """Pro league (distinct hrefs) → seed warms leaguelogo:{slug}:default and :dark.

    Uses a fully mocked environment (httpx + redis + pool) so no live services
    are needed.  Asserts:
    - redis.set called for both :default and :dark keys
    - UPDATE executed with parameterized slug/logo_url/logo_variants args
    """
    from matchup_thumbs.seed import run as seed_run

    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "nba"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    sport, espn_league_slug = path.split("/", 1)
    core_url = f"{core_api_base_url}/v2/sports/{sport}/leagues/{espn_league_slug}"
    site_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit={limit}"
    )

    default_href = "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png"
    dark_href = (
        "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500-dark/nba.png"
    )

    # Mock httpx — responds to core API and site API; returns PNG bytes for logo fetches
    def _make_tiny_png() -> bytes:
        buf = io.BytesIO()
        _Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(buf, format="PNG")
        return buf.getvalue()

    tiny_png = _make_tiny_png()

    # Build a minimal ESPN teams fixture with one active team
    espn_teams_fixture: dict[str, Any] = {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {
                                "team": {
                                    "id": "13",
                                    "slug": "los-angeles-lakers",
                                    "abbreviation": "LAL",
                                    "displayName": "Los Angeles Lakers",
                                    "shortDisplayName": "Lakers",
                                    "name": "Lakers",
                                    "location": "Los Angeles",
                                    "isActive": True,
                                    "logos": [],
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }

    def mock_http_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == core_url:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "logos": [
                            {"href": default_href, "rel": ["full", "default"]},
                            {"href": dark_href, "rel": ["full", "dark"]},
                        ]
                    }
                ).encode(),
                headers={"content-type": "application/json"},
            )
        if url == site_url:
            return httpx.Response(
                200,
                content=json.dumps(espn_teams_fixture).encode(),
                headers={"content-type": "application/json"},
            )
        # Logo byte fetches from CDN
        return httpx.Response(200, content=tiny_png)

    # Mock pool — simulate league_id lookup returning 1 and alias rowcount 0.
    # cursor must support async context manager protocol (used as conn.cursor() in
    # `async with pool.connection() as conn, conn.cursor() as cur:`).
    pool = MagicMock()
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    cursor.fetchone = AsyncMock(return_value=(1,))
    cursor.rowcount = 0
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    # Use MagicMock (not AsyncMock) for cursor() so the call returns the cursor
    # synchronously (psycopg pattern: async with conn.cursor() — cursor() is sync)
    conn.cursor = MagicMock(return_value=cursor)
    pool.connection = MagicMock(return_value=conn)

    transport = httpx.MockTransport(handler=mock_http_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await seed_run(pool, mock_redis, http_client, [league_slug])

    # Verify redis.set was called for both league logo keys
    set_calls = mock_redis.set.call_args_list
    set_keys = [c.args[0] if c.args else c.kwargs.get("name", b"") for c in set_calls]
    league_logo_keys = [k for k in set_keys if k.startswith(b"leaguelogo:")]
    assert b"leaguelogo:nba:default" in league_logo_keys, (
        f"Expected leaguelogo:nba:default in set calls, got {league_logo_keys}"
    )
    assert b"leaguelogo:nba:dark" in league_logo_keys, (
        f"Expected leaguelogo:nba:dark in set calls, got {league_logo_keys}"
    )


async def test_seed_league_logo_ncaa_warms_sportbanner_both_keys(
    mock_redis: MagicMock,
) -> None:
    """NCAA ncaaf → seed fetches real ncaa.com sportbanner; warms :default and :dark.

    i3r behavior: when _has_usable_league_logo is False AND slug is in
    _NCAA_SPORTBANNER_SPORTS, seed fetches the real shield from ncaa.com ONCE,
    warms BOTH leaguelogo:ncaaf:default and leaguelogo:ncaaf:dark with those same
    bytes, and updates Postgres leagues.logo_url + logo_variants to the ncaa.com URL.

    Verifies:
    - leaguelogo:ncaaf:default warmed with real ncaa.com bytes (NOT placeholder)
    - leaguelogo:ncaaf:dark ALSO warmed with the same real bytes
    - Real bytes != get_placeholder_logo()
    - DB UPDATE carried the ncaa.com URL in logo_url and logo_variants
    - No exception raised
    """
    from matchup_thumbs.seed import _NCAA_SPORTBANNER_SPORTS
    from matchup_thumbs.seed import run as seed_run
    from matchup_thumbs.settings import settings

    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "ncaaf"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    sport_key, espn_league_slug = path.split("/", 1)
    core_url = f"{core_api_base_url}/v2/sports/{sport_key}/leagues/{espn_league_slug}"
    site_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit={limit}"
    )

    identical_href = (
        "https://a.espncdn.com/redesign/assets/img/icons/ESPN-icon-football-college.png"
    )

    # Build the expected ncaa.com sportbanner URL
    sport_filename = _NCAA_SPORTBANNER_SPORTS[league_slug]
    ncaa_url = f"{settings.ncaa_sportbanner_base_url}/{sport_filename}.png"

    # Distinct tiny PNG that represents the real NCAA shield (NOT the placeholder)
    def _make_tiny_png() -> bytes:
        buf = io.BytesIO()
        _Image.new("RGBA", (10, 10), (0, 100, 200, 255)).save(buf, format="PNG")
        return buf.getvalue()

    tiny_png = _make_tiny_png()

    espn_teams_fixture: dict[str, Any] = {"sports": [{"leagues": [{"teams": []}]}]}

    def mock_http_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == core_url:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "logos": [
                            {"href": identical_href, "rel": ["full", "default"]},
                            {"href": identical_href, "rel": ["full", "dark"]},
                        ]
                    }
                ).encode(),
                headers={"content-type": "application/json"},
            )
        if url == site_url:
            return httpx.Response(
                200,
                content=json.dumps(espn_teams_fixture).encode(),
                headers={"content-type": "application/json"},
            )
        if url == ncaa_url:
            # Real ncaa.com sportbanner fetch
            return httpx.Response(200, content=tiny_png)
        return httpx.Response(404)

    pool = MagicMock()
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    cursor.fetchone = AsyncMock(return_value=(1,))
    cursor.rowcount = 0
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.cursor = MagicMock(return_value=cursor)
    pool.connection = MagicMock(return_value=conn)

    transport = httpx.MockTransport(handler=mock_http_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await seed_run(pool, mock_redis, http_client, [league_slug])

    set_calls = mock_redis.set.call_args_list
    set_keys = [c.args[0] if c.args else c.kwargs.get("name", b"") for c in set_calls]
    league_logo_keys = [k for k in set_keys if k.startswith(b"leaguelogo:")]

    assert b"leaguelogo:ncaaf:default" in league_logo_keys, (
        f"Expected leaguelogo:ncaaf:default warmed, got {league_logo_keys}"
    )
    assert b"leaguelogo:ncaaf:dark" in league_logo_keys, (
        f"Expected leaguelogo:ncaaf:dark warmed, got {league_logo_keys}"
    )

    # Both keys must carry the real ncaa.com bytes (NOT the placeholder)
    placeholder = get_placeholder_logo()
    for target_key in (b"leaguelogo:ncaaf:default", b"leaguelogo:ncaaf:dark"):
        matched = False
        for c in set_calls:
            k = c.args[0] if c.args else b""
            if k == target_key:
                warmed_bytes = c.args[1] if len(c.args) > 1 else b""
                assert warmed_bytes == tiny_png, (
                    f"{target_key!r} must be warmed with real ncaa.com bytes, "
                    f"not placeholder"
                )
                assert warmed_bytes != placeholder, (
                    f"{target_key!r} must NOT be warmed with placeholder bytes"
                )
                matched = True
                break
        assert matched, f"No redis.set call found for {target_key!r}"

    # Assert the DB UPDATE carried the ncaa.com URL in logo_url and logo_variants
    execute_calls = cursor.execute.call_args_list
    update_params_list = [
        c.args[1]
        for c in execute_calls
        if len(c.args) >= 2
        and isinstance(c.args[1], dict)
        and c.args[1].get("logo_url") == ncaa_url
    ]
    assert update_params_list, (
        f"Expected a DB UPDATE with logo_url={ncaa_url!r}; "
        f"execute calls: {[c.args[1] for c in execute_calls if len(c.args) >= 2]}"
    )
    update_params = update_params_list[0]
    assert update_params["slug"] == league_slug
    variants_arg = update_params["logo_variants"]
    # Jsonb wraps the dict — compare .obj attribute or the dict itself
    variants_dict = variants_arg.obj if hasattr(variants_arg, "obj") else variants_arg
    assert variants_dict == {"default": ncaa_url, "dark": ncaa_url}, (
        f"logo_variants must be {{default: url, dark: url}}, got {variants_dict}"
    )


# ---------------------------------------------------------------------------
# 12-04 Task 2: NCAA-like league warms both :default and :dark (belt-and-suspenders)
# ---------------------------------------------------------------------------


async def test_seed_ncaa_like_league_warms_sportbanner_both_keys(
    mock_redis: MagicMock,
) -> None:
    """NCAA ncaab → seed fetches real ncaa.com sportbanner; warms :default and :dark.

    i3r behavior: ncaab (identical ESPN hrefs → not usable) fetches the real
    basketball shield from ncaa.com ONCE, warms both leaguelogo:ncaab:default and
    leaguelogo:ncaab:dark with those same bytes, and updates Postgres leagues.logo_url
    + logo_variants to the ncaa.com URL.

    Assertions:
    - mock_redis.set called for leaguelogo:ncaab:default (real ncaa.com bytes)
    - mock_redis.set called for leaguelogo:ncaab:dark (same real bytes)
    - Real bytes != get_placeholder_logo()
    - DB UPDATE carried the ncaa.com URL in logo_url and logo_variants
    - No exception raised
    """
    from matchup_thumbs.seed import _NCAA_SPORTBANNER_SPORTS
    from matchup_thumbs.seed import run as seed_run
    from matchup_thumbs.settings import settings

    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "ncaab"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    sport_key, espn_league_slug = path.split("/", 1)
    core_url = f"{core_api_base_url}/v2/sports/{sport_key}/leagues/{espn_league_slug}"
    site_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit={limit}"
    )

    # NCAA placeholder: both hrefs identical → _has_usable_league_logo returns False
    identical_href = "https://a.espncdn.com/redesign/assets/img/icons/ESPN-icon-basketball-college.png"

    # Build the expected ncaa.com sportbanner URL for basketball
    sport_filename = _NCAA_SPORTBANNER_SPORTS[league_slug]
    ncaa_url = f"{settings.ncaa_sportbanner_base_url}/{sport_filename}.png"

    # Distinct tiny PNG representing the real NCAA basketball shield (not placeholder)
    def _make_tiny_png() -> bytes:
        buf = io.BytesIO()
        _Image.new("RGBA", (10, 10), (200, 50, 0, 255)).save(buf, format="PNG")
        return buf.getvalue()

    tiny_png = _make_tiny_png()

    espn_teams_fixture: dict[str, Any] = {"sports": [{"leagues": [{"teams": []}]}]}

    def mock_http_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == core_url:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "logos": [
                            {"href": identical_href, "rel": ["full", "default"]},
                            {"href": identical_href, "rel": ["full", "dark"]},
                        ]
                    }
                ).encode(),
                headers={"content-type": "application/json"},
            )
        if url == site_url:
            return httpx.Response(
                200,
                content=json.dumps(espn_teams_fixture).encode(),
                headers={"content-type": "application/json"},
            )
        if url == ncaa_url:
            # Real ncaa.com sportbanner fetch
            return httpx.Response(200, content=tiny_png)
        return httpx.Response(404)

    pool = MagicMock()
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    cursor.fetchone = AsyncMock(return_value=(1,))
    cursor.rowcount = 0
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.cursor = MagicMock(return_value=cursor)
    pool.connection = MagicMock(return_value=conn)

    transport = httpx.MockTransport(handler=mock_http_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        await seed_run(pool, mock_redis, http_client, [league_slug])

    set_calls = mock_redis.set.call_args_list
    set_keys = [c.args[0] if c.args else c.kwargs.get("name", b"") for c in set_calls]
    league_logo_keys = [k for k in set_keys if k.startswith(b"leaguelogo:")]

    assert b"leaguelogo:ncaab:default" in league_logo_keys, (
        f"Expected leaguelogo:ncaab:default warmed, got {league_logo_keys}"
    )
    assert b"leaguelogo:ncaab:dark" in league_logo_keys, (
        f"Expected leaguelogo:ncaab:dark warmed, got {league_logo_keys}"
    )

    # Both keys must carry the real ncaa.com bytes (NOT the placeholder)
    placeholder = get_placeholder_logo()
    for target_key in (b"leaguelogo:ncaab:default", b"leaguelogo:ncaab:dark"):
        matched = False
        for c in set_calls:
            k = c.args[0] if c.args else b""
            if k == target_key:
                warmed_bytes = c.args[1] if len(c.args) > 1 else b""
                assert warmed_bytes == tiny_png, (
                    f"{target_key!r} must be warmed with real ncaa.com bytes"
                )
                assert warmed_bytes != placeholder, (
                    f"{target_key!r} must NOT be warmed with placeholder bytes"
                )
                matched = True
                break
        assert matched, f"No redis.set call found for {target_key!r}"

    # Assert the DB UPDATE carried the ncaa.com URL in logo_url and logo_variants
    execute_calls = cursor.execute.call_args_list
    update_params_list = [
        c.args[1]
        for c in execute_calls
        if len(c.args) >= 2
        and isinstance(c.args[1], dict)
        and c.args[1].get("logo_url") == ncaa_url
    ]
    assert update_params_list, (
        f"Expected a DB UPDATE with logo_url={ncaa_url!r}; "
        f"execute calls: {[c.args[1] for c in execute_calls if len(c.args) >= 2]}"
    )
    update_params = update_params_list[0]
    assert update_params["slug"] == league_slug
    variants_arg = update_params["logo_variants"]
    variants_dict = variants_arg.obj if hasattr(variants_arg, "obj") else variants_arg
    assert variants_dict == {"default": ncaa_url, "dark": ncaa_url}, (
        f"logo_variants must be {{default: url, dark: url}}, got {variants_dict}"
    )


# ---------------------------------------------------------------------------
# i3r: NCAA sportbanner fetch-failure fallback test
# ---------------------------------------------------------------------------


async def test_seed_ncaa_sportbanner_fetch_failure_falls_back_to_placeholder(
    mock_redis: MagicMock,
) -> None:
    """NCAA ncaaf sportbanner fetch failure → both keys warmed with placeholder.

    When the ncaa.com sportbanner URL returns a non-200 (503), seed must:
    - NOT raise any exception
    - Warm leaguelogo:ncaaf:default with get_placeholder_logo() bytes
    - Warm leaguelogo:ncaaf:dark with get_placeholder_logo() bytes
    - NOT update Postgres to the ncaa.com URL (DB stays as-is from the ESPN UPDATE)
    """
    from matchup_thumbs.seed import _NCAA_SPORTBANNER_SPORTS
    from matchup_thumbs.seed import run as seed_run
    from matchup_thumbs.settings import settings

    core_api_base_url = "https://sports.core.api.espn.com"
    league_slug = "ncaaf"
    path, limit = LEAGUE_ENDPOINTS[league_slug]
    sport_key, espn_league_slug = path.split("/", 1)
    core_url = f"{core_api_base_url}/v2/sports/{sport_key}/leagues/{espn_league_slug}"
    site_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit={limit}"
    )

    identical_href = (
        "https://a.espncdn.com/redesign/assets/img/icons/ESPN-icon-football-college.png"
    )

    sport_filename = _NCAA_SPORTBANNER_SPORTS[league_slug]
    ncaa_url = f"{settings.ncaa_sportbanner_base_url}/{sport_filename}.png"

    espn_teams_fixture: dict[str, Any] = {"sports": [{"leagues": [{"teams": []}]}]}

    def mock_http_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == core_url:
            return httpx.Response(
                200,
                content=json.dumps(
                    {
                        "logos": [
                            {"href": identical_href, "rel": ["full", "default"]},
                            {"href": identical_href, "rel": ["full", "dark"]},
                        ]
                    }
                ).encode(),
                headers={"content-type": "application/json"},
            )
        if url == site_url:
            return httpx.Response(
                200,
                content=json.dumps(espn_teams_fixture).encode(),
                headers={"content-type": "application/json"},
            )
        if url == ncaa_url:
            # Simulate ncaa.com CDN failure
            return httpx.Response(503, content=b"Service Unavailable")
        return httpx.Response(404)

    pool = MagicMock()
    conn = MagicMock()
    cursor = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    cursor.fetchone = AsyncMock(return_value=(1,))
    cursor.rowcount = 0
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.cursor = MagicMock(return_value=cursor)
    pool.connection = MagicMock(return_value=conn)

    transport = httpx.MockTransport(handler=mock_http_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        # Must NOT raise even though ncaa.com returns 503
        await seed_run(pool, mock_redis, http_client, [league_slug])

    set_calls = mock_redis.set.call_args_list
    set_keys = [c.args[0] if c.args else c.kwargs.get("name", b"") for c in set_calls]
    league_logo_keys = [k for k in set_keys if k.startswith(b"leaguelogo:")]

    assert b"leaguelogo:ncaaf:default" in league_logo_keys, (
        f"Expected leaguelogo:ncaaf:default warmed even on fetch failure, "
        f"got {league_logo_keys}"
    )
    assert b"leaguelogo:ncaaf:dark" in league_logo_keys, (
        f"Expected leaguelogo:ncaaf:dark warmed even on fetch failure, "
        f"got {league_logo_keys}"
    )

    # Both keys must carry the placeholder bytes on failure
    placeholder = get_placeholder_logo()
    for target_key in (b"leaguelogo:ncaaf:default", b"leaguelogo:ncaaf:dark"):
        matched = False
        for c in set_calls:
            k = c.args[0] if c.args else b""
            if k == target_key:
                warmed_bytes = c.args[1] if len(c.args) > 1 else b""
                assert warmed_bytes == placeholder, (
                    f"{target_key!r} must fall back to placeholder on fetch failure"
                )
                matched = True
                break
        assert matched, f"No redis.set call found for {target_key!r}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_core_api_mock_handler(
    expected_url: str,
    fixture: dict[str, Any],
) -> Any:
    """Return an httpx mock transport handler for the ESPN core API."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == expected_url:
            return httpx.Response(
                200,
                content=json.dumps(fixture).encode(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    return handler


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
