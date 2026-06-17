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
    # Bumping render_version invalidates the Redis render tier (new :v{N} key; old
    # entries become unreachable and expire by their TTL — no flush needed, CACHE-07).
    # NOTE: nginx proxy_cache is NOT invalidated by this bump; its key is URL-based
    # and nginx entries expire on their own 30-day TTL (RESEARCH Pitfall 4).
    render_version: int = 4  # v1.3 bump: 3 → 4 — league-logo + poster seam output
    # (VS→logo replacement on thumb/poster and GaussianBlur poster seam changed
    #  rendered output; bump retires stale Redis :v3 blobs so deployed instances
    #  re-render instead of serving the old VS-wordmark PNGs until their 30-day TTL).

    # ESPN integration
    espn_base_url: str = "https://site.api.espn.com"
    # ESPN core API base — DISTINCT from espn_base_url (site.api.espn.com).
    # The core API hosts league-root objects including the inline `logos` array
    # (LGL-01, D-01, RESEARCH Pitfall 2).  Never conflate the two base URLs.
    espn_core_api_base_url: str = "https://sports.core.api.espn.com"
    espn_request_timeout: float = 10.0
    espn_semaphore_size: int = 5  # D-08: conservative starting point
    espn_jitter_max: float = 0.5  # seconds; random delay between CDN logo fetches
    # Sanctioned second public source for NCAA league shields (ncaaf, ncaab).
    # ESPN returns only a generic same-URL icon for NCAA leagues; ncaa.com's
    # sportbanner CDN supplies the real per-sport shield (see CLAUDE.md note).
    # No trailing slash — sport filename appended as "/{sport}.png".
    ncaa_sportbanner_base_url: str = (
        "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners"
    )

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

    # Contrast engine (Phase 9 — D-04, D-05)
    min_contrast_ratio: float = 3.0  # WCAG SC 1.4.11 Non-text Contrast (D-04)

    # Readiness probe (Phase 4 — D-15, API-06)
    readyz_check_timeout: float = 3.0  # seconds; per-check timeout in /readyz


settings = Settings()
