"""Tests for the settings model."""

from matchup_thumbs.settings import Settings


def test_settings_loads_from_env(test_settings: Settings) -> None:
    """Settings reads POSTGRES_DSN from environment and exposes it correctly."""
    assert "matchup" in str(test_settings.postgres_dsn)


def test_render_version_is_4() -> None:
    """render_version is 4 — bumped from 3 to invalidate all v1.2.x cached renders.

    Asserts CACHE-08: the v1.3 bump (VS→logo + poster seam) is reflected in the
    settings model so the render key is :v4, making all prior :v3 keys unreachable.
    """
    assert Settings().render_version == 4
