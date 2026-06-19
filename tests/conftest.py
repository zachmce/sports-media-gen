"""Shared pytest fixtures for matchup-thumbs tests."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from matchup_thumbs.contrast import ContrastDecision, SelectionReason, Treatment
from matchup_thumbs.generators.types import DecodedAssets
from matchup_thumbs.main import app
from matchup_thumbs.settings import Settings

# ---------------------------------------------------------------------------
# Postgres availability guard (shared with test_seed.py, test_resolver.py)
# ---------------------------------------------------------------------------

_POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")

_PG_AVAILABLE: bool = False
if _POSTGRES_DSN:
    try:
        _ping_dsn = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(_ping_dsn, connect_timeout=3):
            _PG_AVAILABLE = True
    except Exception:
        _PG_AVAILABLE = False

_SKIP_REASON: str = (
    "No live Postgres reachable.  "
    "Set POSTGRES_DSN=postgresql+psycopg://<user>:<pass>@<host>:<port>/<db> to enable."
)

#: Mark a test as requiring live Postgres; skip it automatically when not available.
#: Import this in test_seed.py and test_resolver.py instead of re-deriving it.
pg_required = pytest.mark.skipif(not _PG_AVAILABLE, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Existing fixtures (must not be removed)
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide a Settings instance with test DSN values via monkeypatched env.

    Note: this fixture constructs a *new* Settings instance from the patched
    env and returns it for direct inspection.  It does NOT affect the
    module-level ``settings`` singleton already consumed by ``main.py``.
    Tests that need to verify Settings parsing should use this fixture; tests
    that need to interact with the running app via ``client`` use the stub
    lifespan in that fixture instead.
    """
    monkeypatch.setenv(
        "POSTGRES_DSN",
        "postgresql+psycopg://matchup:matchup@localhost:5432/matchup_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    return Settings()


@asynccontextmanager
async def _stub_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan stub that injects mock DB pool and Redis without real connections.

    Allows the app tests (health route, import smoke) to run without a live
    Postgres or Redis.  DB-integration tests live in ``test_migrations.py``
    and use a separate skip-guard mechanism.
    """
    pool = MagicMock()
    pool.close = AsyncMock()
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    http_client = MagicMock()
    http_client.aclose = AsyncMock()

    app.state.db_pool = pool
    app.state.redis = redis_client
    app.state.http_client = http_client
    yield


@pytest.fixture
def client() -> Generator[TestClient]:
    """Yield a TestClient backed by a stub lifespan (no live DB/Redis required).

    The real lifespan is replaced for the duration of the test so that
    ``uv run pytest -q`` passes without any external services running.
    DB-integration tests are gated separately in ``test_migrations.py``.
    """
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _stub_lifespan
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original_lifespan


# ---------------------------------------------------------------------------
# New fixtures for Phase 2
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a MagicMock-backed async connection pool for unit tests.

    Provides the async context manager protocol expected by resolver.py and
    seed.py without hitting a real Postgres instance.
    """
    pool = MagicMock()
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    cursor.fetchone = AsyncMock(return_value=None)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    # Use a plain MagicMock (not AsyncMock) so that conn.cursor(row_factory=...)
    # returns the cursor mock directly (as a sync context manager target), rather
    # than returning a coroutine that Python cannot use with ``async with`` without
    # an explicit ``await`` first.
    conn.cursor = MagicMock(return_value=cursor)
    pool.connection.return_value = conn
    return pool


@pytest.fixture
def mock_redis() -> MagicMock:
    """Return a MagicMock-backed async Redis client for unit tests.

    Returns None from get() (cache miss) and records set() / delete() calls
    without hitting a real Redis instance.

    Phase 3 extensions:
    - redis.delete = AsyncMock() for singleflight lock release tests.
    - redis.set returns None by default (lock not acquired in NX tests).
      Override return_value=True in individual tests to simulate acquisition.
    """
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# Phase 3 fixtures: team dicts and decoded assets for generator / render tests
# ---------------------------------------------------------------------------


def fixture_lakers() -> dict[str, Any]:
    """Return a TeamDict-compatible dict for the NBA Los Angeles Lakers.

    Values match the seeded_registry fixture (phase 2) so tests that use
    this helper alongside DB integration tests stay consistent.
    """
    return {
        "id": 1,
        "league_id": 1,
        "slug": "los-angeles-lakers",
        "display_name": "Los Angeles Lakers",
        "abbreviation": "LAL",
        "primary_color": "#552583",
        "secondary_color": "#fdb927",
        "logo_url": None,
        "provider_id": "13",
        "logo_variants": None,
    }


def fixture_clippers() -> dict[str, Any]:
    """Return a TeamDict-compatible dict for the NBA Los Angeles Clippers.

    Values match the seeded_registry fixture (phase 2) so tests that use
    this helper alongside DB integration tests stay consistent.
    """
    return {
        "id": 2,
        "league_id": 1,
        "slug": "los-angeles-clippers",
        "display_name": "Los Angeles Clippers",
        "abbreviation": "LAC",
        "primary_color": "#c8102e",
        "secondary_color": "#1d428a",
        "logo_url": None,
        "provider_id": "12",
        "logo_variants": None,
    }


def make_decision(
    background_rgb: tuple[int, int, int] = (85, 37, 131),  # Lakers purple default
    background_source: str = "primary",
    achieved_ratio: float = 6.1,
    recommended_variant: str | None = None,
    treatment: Treatment | None = None,
    reason: SelectionReason | None = None,
) -> ContrastDecision:
    """Return a ContrastDecision with sensible defaults for test fixtures.

    Phase 10 D-02.  Override only the fields relevant to the test being written.
    treatment defaults to Treatment.NONE; reason defaults to SelectionReason.PRIMARY_OK.
    """
    return ContrastDecision(
        background_rgb=background_rgb,
        background_source=background_source,
        achieved_ratio=achieved_ratio,
        recommended_variant=recommended_variant,
        treatment=treatment if treatment is not None else Treatment.NONE,
        reason=reason if reason is not None else SelectionReason.PRIMARY_OK,
    )


def fixture_decoded_assets() -> DecodedAssets:
    """Return a DecodedAssets dict with logos and default ContrastDecisions.

    Phase 10 D-02.  Uses Lakers purple and Clippers red so colour-fallback
    tests can distinguish team regions in the generated image.  No ESPN call
    needed.
    """
    away_logo = Image.new("RGBA", (200, 200), (85, 37, 131, 255))  # Lakers purple
    home_logo = Image.new("RGBA", (200, 200), (200, 16, 46, 255))  # Clippers red
    return DecodedAssets(
        away_logo=away_logo,
        home_logo=home_logo,
        away_decision=make_decision(background_rgb=(85, 37, 131)),  # Lakers purple
        home_decision=make_decision(background_rgb=(200, 16, 46)),  # Clippers red
        league_logo=None,  # Phase 11 (D-08) — not yet consumed by generators
        league_decision=None,  # Phase 12 (D-05) — do NOT remove league_logo=None
    )


def _make_synthetic_league_logo() -> Image.Image:
    """Return a wide RGBA wordmark-shaped image (2:1 aspect) for fixture use.

    The 200×100 size exercises the aspect-preserving contain-fit in the
    generators (_LEAGUE_LOGO_BOX × _LEAGUE_LOGO_BOX bounding box).  Solid
    white fill simulates a light wordmark against a coloured seam.
    """
    return Image.new("RGBA", (200, 100), (255, 255, 255, 255))


def fixture_decoded_assets_with_league_logo() -> DecodedAssets:
    """Return a DecodedAssets dict with a synthetic league logo for logo-path tests.

    Builds on fixture_decoded_assets() (league_logo=None / league_decision=None)
    and injects a wide white wordmark plus a seam-referenced ContrastDecision.
    The existing fixture_decoded_assets() is NOT modified (Pitfall 1 — modifying
    the base fixture would break all VS-fallback golden comparisons).

    The seam_rgb (142, 26, 88) is the 50/50 blend of:
      Lakers purple (85, 37, 131) and Clippers red (200, 16, 46),
    matching the value _blend_seam_color() would compute at render time.
    Treatment.NONE: the white logo has sufficient contrast on the purple-red seam.
    """
    assets = fixture_decoded_assets()
    seam_rgb: tuple[int, int, int] = (142, 26, 88)  # mid-blend of Lakers/Clippers
    assets["league_logo"] = _make_synthetic_league_logo()
    assets["league_decision"] = make_decision(
        background_rgb=seam_rgb,
        background_source="seam",
        treatment=Treatment.NONE,
    )
    return assets


@pytest.fixture
def espn_nba_fixture() -> dict[str, Any]:
    """Load and return the recorded ESPN NBA teams response dict.

    Provides a deterministic, offline-capable input for seed tests that
    mock the httpx ESPN call with pytest-httpx.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "espn_nba_response.json"
    with fixture_path.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture
def seeded_registry(request: pytest.FixtureRequest) -> Generator[None]:
    """Idempotently upsert a minimal team set into the live test Postgres DB.

    Inserts NBA Lakers, NBA Clippers, and NFL Chargers with aliases required by
    the resolver acceptance tests.  Cleans up on teardown so the suite is
    repeatable.

    Skipped automatically when Postgres is not reachable (pg_required guard).
    Teams seeded:
    - NBA Los Angeles Lakers (aliases: lakers, lal, losangeles, losangeleslakers)
    - NBA Los Angeles Clippers (alias: lac)
    - NFL Los Angeles Chargers (alias: lac)
    These exercise league-scope isolation (same alias, two leagues) and fuzzy
    resolution (lakerz, LA-Lakers).
    """
    if not _PG_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    raw_dsn = _POSTGRES_DSN.replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(raw_dsn) as conn:
        with conn.cursor() as cur:
            # Look up league IDs
            cur.execute("SELECT slug, id FROM leagues WHERE slug IN ('nba', 'nfl')")
            league_map: dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}
            nba_id = league_map["nba"]
            nfl_id = league_map["nfl"]

            # Upsert Lakers
            cur.execute(
                """
                INSERT INTO teams
                    (league_id, slug, display_name, abbreviation,
                     primary_color, secondary_color, provider_id)
                VALUES (%(league_id)s, %(slug)s, %(display_name)s,
                        %(abbreviation)s, %(primary_color)s,
                        %(secondary_color)s, %(provider_id)s)
                ON CONFLICT (league_id, slug) DO UPDATE SET
                    display_name    = EXCLUDED.display_name,
                    abbreviation    = EXCLUDED.abbreviation,
                    primary_color   = EXCLUDED.primary_color,
                    secondary_color = EXCLUDED.secondary_color,
                    provider_id     = EXCLUDED.provider_id
                RETURNING id
                """,
                {
                    "league_id": nba_id,
                    "slug": "los-angeles-lakers",
                    "display_name": "Los Angeles Lakers",
                    "abbreviation": "LAL",
                    "primary_color": "#552583",
                    "secondary_color": "#fdb927",
                    "provider_id": "13",
                },
            )
            row = cur.fetchone()
            assert row is not None
            lakers_id: int = row[0]

            # Upsert Clippers
            cur.execute(
                """
                INSERT INTO teams
                    (league_id, slug, display_name, abbreviation,
                     primary_color, secondary_color, provider_id)
                VALUES (%(league_id)s, %(slug)s, %(display_name)s,
                        %(abbreviation)s, %(primary_color)s,
                        %(secondary_color)s, %(provider_id)s)
                ON CONFLICT (league_id, slug) DO UPDATE SET
                    display_name    = EXCLUDED.display_name,
                    abbreviation    = EXCLUDED.abbreviation,
                    primary_color   = EXCLUDED.primary_color,
                    secondary_color = EXCLUDED.secondary_color,
                    provider_id     = EXCLUDED.provider_id
                RETURNING id
                """,
                {
                    "league_id": nba_id,
                    "slug": "los-angeles-clippers",
                    "display_name": "Los Angeles Clippers",
                    "abbreviation": "LAC",
                    "primary_color": "#c8102e",
                    "secondary_color": "#1d428a",
                    "provider_id": "12",
                },
            )
            row = cur.fetchone()
            assert row is not None
            clippers_id: int = row[0]

            # Upsert NFL Chargers
            cur.execute(
                """
                INSERT INTO teams
                    (league_id, slug, display_name, abbreviation,
                     primary_color, secondary_color, provider_id)
                VALUES (%(league_id)s, %(slug)s, %(display_name)s,
                        %(abbreviation)s, %(primary_color)s,
                        %(secondary_color)s, %(provider_id)s)
                ON CONFLICT (league_id, slug) DO UPDATE SET
                    display_name    = EXCLUDED.display_name,
                    abbreviation    = EXCLUDED.abbreviation,
                    primary_color   = EXCLUDED.primary_color,
                    secondary_color = EXCLUDED.secondary_color,
                    provider_id     = EXCLUDED.provider_id
                RETURNING id
                """,
                {
                    "league_id": nfl_id,
                    "slug": "los-angeles-chargers",
                    "display_name": "Los Angeles Chargers",
                    "abbreviation": "LAC",
                    "primary_color": "#0073cf",
                    "secondary_color": "#ffb612",
                    "provider_id": "24",
                },
            )
            row = cur.fetchone()
            assert row is not None
            chargers_id: int = row[0]

            # Upsert aliases — ON CONFLICT DO NOTHING (D-12)
            lakers_aliases = [
                "lakers",
                "lal",
                "losangeles",
                "losangeleslakers",
            ]
            for alias in lakers_aliases:
                cur.execute(
                    """
                    INSERT INTO team_aliases (team_id, league_id, alias)
                    VALUES (%(team_id)s, %(league_id)s, %(alias)s)
                    ON CONFLICT (league_id, alias) DO NOTHING
                    """,
                    {"team_id": lakers_id, "league_id": nba_id, "alias": alias},
                )

            cur.execute(
                """
                INSERT INTO team_aliases (team_id, league_id, alias)
                VALUES (%(team_id)s, %(league_id)s, %(alias)s)
                ON CONFLICT (league_id, alias) DO NOTHING
                """,
                {"team_id": clippers_id, "league_id": nba_id, "alias": "lac"},
            )

            cur.execute(
                """
                INSERT INTO team_aliases (team_id, league_id, alias)
                VALUES (%(team_id)s, %(league_id)s, %(alias)s)
                ON CONFLICT (league_id, alias) DO NOTHING
                """,
                {"team_id": chargers_id, "league_id": nfl_id, "alias": "lac"},
            )

        conn.commit()

    yield

    # Teardown: remove seeded test rows
    with psycopg.connect(raw_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM team_aliases
                WHERE team_id IN (
                    SELECT id FROM teams
                    WHERE slug IN (
                        'los-angeles-lakers',
                        'los-angeles-clippers',
                        'los-angeles-chargers'
                    )
                )
                """
            )
            cur.execute(
                """
                DELETE FROM teams
                WHERE slug IN (
                    'los-angeles-lakers',
                    'los-angeles-clippers',
                    'los-angeles-chargers'
                )
                """
            )
        conn.commit()
