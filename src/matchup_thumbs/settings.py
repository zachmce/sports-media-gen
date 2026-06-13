"""Application settings via pydantic-settings."""

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    postgres_dsn: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://matchup:matchup@localhost:5432/matchup"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    log_level: str = "INFO"
    render_version: int = 1


settings = Settings()
