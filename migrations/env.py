"""Alembic async migration environment using psycopg3.

The database URL is read from the POSTGRES_DSN environment variable (the same
variable used by the FastAPI app's pydantic-settings Settings model).  If
POSTGRES_DSN is not set, the URL is constructed from the individual DB_* env
vars (DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME) to match the Compose
`migrate` service configuration.

Alembic MUST run as a separate process (the `migrate` Compose service); never
call alembic upgrade from inside the FastAPI process — asyncio.run() raises
RuntimeError if called inside a running event loop (Pitfall 1).
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object: access to values within the .ini file.
config = context.config

# Configure Python logging from the .ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM metadata — Alembic autogenerate is not used; schema is authored by hand.
target_metadata = None


def _get_database_url() -> str:
    """Resolve the database URL from environment variables.

    Priority:
    1. POSTGRES_DSN — the canonical app-wide DSN (pydantic-settings convention)
    2. DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME — Compose `migrate` service env
    3. Fallback to the alembic.ini sqlalchemy.url value (for offline SQL generation)
    """
    if dsn := os.environ.get("POSTGRES_DSN"):
        return dsn
    user = os.environ.get("DB_USER", "")
    password = os.environ.get("DB_PASSWORD", "")
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "matchup")
    if user and password:
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    # Fallback: let alembic.ini provide the URL (offline mode / unit testing)
    return config.get_main_option("sqlalchemy.url") or ""


# Override the URL before Alembic reads the config section.
config.set_main_option("sqlalchemy.url", _get_database_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using an established synchronous connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations via run_sync."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using asyncio.run."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
