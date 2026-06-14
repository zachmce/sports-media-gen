"""Application settings via pydantic-settings."""

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Docker Compose .env files carry service-level vars (POSTGRES_USER,
        # POSTGRES_PASSWORD, API_HOST_PORT, etc.) that the app does not consume
        # directly.  Ignoring extras prevents ValidationError when the shared
        # .env is present on the host.
        extra="ignore",
    )

    postgres_dsn: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://matchup:matchup@localhost:5432/matchup"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    log_level: str = "INFO"
    render_version: int = 1

    # ESPN integration
    espn_base_url: str = "https://site.api.espn.com"
    espn_request_timeout: float = 10.0
    espn_semaphore_size: int = 5  # D-08: conservative starting point
    espn_jitter_max: float = 0.5  # seconds; random delay between CDN logo fetches

    # Seed behaviour
    seed_leagues: str = "nba,nfl,mlb,nhl,ncaaf,ncaab"  # comma-separated; all by default
    logo_cache_ttl: int = 30 * 24 * 3600  # 30 days in seconds

    # Resolver
    resolve_similarity_threshold: float = 0.5  # D-13
    resolve_positive_ttl: int = 7 * 24 * 3600  # 7 days (RES-05)
    resolve_negative_ttl: int = 5 * 60  # 5 minutes (D-14)

    # Render cache + singleflight (Phase 3 — D-12, D-13, D-14)
    # 30 days; render blob TTL, mirrors logo_cache_ttl magnitude (D-12)
    render_cache_ttl: int = 30 * 24 * 3600
    sf_lock_ttl: int = 10  # seconds; singleflight SET NX lock TTL (D-13)
    sf_poll_interval: float = 0.05  # seconds; waiter poll cadence (D-13)
    # seconds; max waiter wait before degraded local render (D-13/D-14)
    sf_max_wait: float = 5.0


settings = Settings()
