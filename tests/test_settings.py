"""Tests for the settings model."""

from matchup_thumbs.settings import Settings


def test_settings_loads_from_env(test_settings: Settings) -> None:
    """Settings reads POSTGRES_DSN from environment and exposes it correctly."""
    assert "matchup" in str(test_settings.postgres_dsn)


def test_render_version_is_5() -> None:
    """render_version is 5 — bumped from 4 to invalidate all v1.3.x cached renders.

    Asserts CACHE-08: the v2.0 bump (soft drop shadow replaces the logo halo) is
    reflected in the settings model so the render key is :v5, making all prior :v4
    keys unreachable.
    """
    assert Settings().render_version == 5
