"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .routes import health
from .settings import settings

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: open/close DB pool and Redis client.

    Uses AsyncExitStack so every registered cleanup callback runs even if an
    earlier one raises — preventing resource leaks on partial startup or
    non-clean shutdown.
    """
    # psycopg3 conninfo uses postgresql:// scheme; strip the SQLAlchemy +psycopg suffix
    conninfo = str(settings.postgres_dsn).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        open=False,
    )

    async with AsyncExitStack() as stack:
        await pool.open()
        stack.push_async_callback(pool.close)

        redis_client: Redis[bytes] = Redis.from_url(
            str(settings.redis_url), decode_responses=False
        )
        stack.push_async_callback(redis_client.aclose)

        app.state.db_pool = pool
        app.state.redis = redis_client

        await logger.ainfo(
            "startup complete",
            pool_min=settings.db_pool_min_size,
            pool_max=settings.db_pool_max_size,
        )
        yield

    await logger.ainfo("shutdown complete")


app = FastAPI(title="matchup-thumbs", lifespan=lifespan)
app.include_router(health.router)


def main() -> None:
    """Dev-only entrypoint for the ``api`` project script.

    Production uses the Dockerfile CMD (gunicorn + UvicornWorker).
    """
    import warnings

    warnings.warn(
        "Running via bare uvicorn. For production, use the Dockerfile CMD "
        "(gunicorn + UvicornWorker).",
        stacklevel=1,
    )
    uvicorn.run(
        "matchup_thumbs.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
