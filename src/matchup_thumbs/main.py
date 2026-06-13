"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .routes import health
from .settings import settings

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan: open/close DB pool and Redis client."""
    # Startup
    pool = AsyncConnectionPool(
        conninfo=str(settings.postgres_dsn),
        min_size=2,
        max_size=10,
        open=False,
    )
    await pool.open()

    redis_client = Redis.from_url(
        str(settings.redis_url), decode_responses=False
    )

    app.state.db_pool = pool
    app.state.redis = redis_client

    await logger.ainfo("startup complete", pool_min=2, pool_max=10)
    yield

    # Shutdown
    await pool.close()
    await redis_client.aclose()
    await logger.ainfo("shutdown complete")


app = FastAPI(title="matchup-thumbs", lifespan=lifespan)
app.include_router(health.router)


def main() -> None:
    """Entrypoint for the `api` project script."""
    uvicorn.run(
        "matchup_thumbs.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
