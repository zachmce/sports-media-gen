"""Tests for the settings model."""

import pytest

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


def test_render_cache_enabled_defaults_true() -> None:
    """render_cache_enabled defaults to True (CACHE-09 regression guard, criterion 1).

    When RENDER_CACHE_ENABLED is unset, the render tier behaves exactly as v2.0.
    Default True is required: absent env var must not disable caching silently.
    """
    assert Settings().render_cache_enabled is True


def test_render_cache_enabled_parses_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """RENDER_CACHE_ENABLED=false coerces to bool False via Pydantic v2 (CACHE-09).

    Confirms env-only, restart-only toggle: set RENDER_CACHE_ENABLED=false to
    engage the kill-switch with no code change or image rebuild (criterion 4).
    """
    monkeypatch.setenv("RENDER_CACHE_ENABLED", "false")
    assert Settings().render_cache_enabled is False
