"""Tests for the settings model."""

from matchup_thumbs.settings import Settings


def test_settings_loads_from_env(test_settings: Settings) -> None:
    """Settings reads POSTGRES_DSN from environment and exposes it correctly."""
    assert "matchup" in str(test_settings.postgres_dsn)
