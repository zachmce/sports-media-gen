"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from .middleware import RequestLoggingMiddleware
from .render import BadTransformParam, UnknownGeneratorError
from .routes import health, images, leagues
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

        redis_client = Redis.from_url(str(settings.redis_url), decode_responses=False)
        stack.push_async_callback(redis_client.aclose)

        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.espn_request_timeout),
            transport=httpx.AsyncHTTPTransport(retries=2),
            follow_redirects=True,
        )
        stack.push_async_callback(http_client.aclose)

        app.state.db_pool = pool
        app.state.redis = redis_client
        app.state.http_client = http_client

        await logger.ainfo(
            "startup complete",
            pool_min=settings.db_pool_min_size,
            pool_max=settings.db_pool_max_size,
        )
        yield

    await logger.ainfo("shutdown complete")


app = FastAPI(title="matchup-thumbs", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Prometheus instrumentation (D-10, OBS-01) — before routers (Pitfall 5)
# ---------------------------------------------------------------------------
# instrument() adds PrometheusInstrumentatorMiddleware; expose() registers
# GET /metrics as a standard FastAPI route.  Both must be called before
# include_router so the route table is complete at first request.
#
# Note for Phase 5: /metrics must NOT be rate-limited in the nginx config
# (proxy_cache zone excludes /metrics alongside /healthz and /readyz).
instrumentator = Instrumentator(
    should_group_status_codes=False,  # keep 200/404/400/503 distinct
    should_ignore_untemplated=True,  # skip metrics for unknown-path 404s
    excluded_handlers=["/metrics"],  # don't record the scrape endpoint itself
)
instrumentator.instrument(app).expose(app, include_in_schema=False)

# RequestLoggingMiddleware added AFTER instrument() so it runs OUTERMOST
# (Starlette: last add_middleware = outermost in execution order).
# Outer → RequestLogging → Prometheus → route handler → Prometheus → RequestLogging
app.add_middleware(RequestLoggingMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers (D-07/D-08) — registered before routers
# ---------------------------------------------------------------------------


@app.exception_handler(UnknownGeneratorError)
async def unknown_generator_handler(
    request: Request, exc: UnknownGeneratorError
) -> JSONResponse:
    """Map UnknownGeneratorError → HTTP 400 with D-07 body (D-08)."""
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "error": "unknown_generator",
                "kind": exc.kind,
                "style": exc.style,
            }
        },
    )


@app.exception_handler(BadTransformParam)
async def bad_transform_param_handler(
    request: Request, exc: BadTransformParam
) -> JSONResponse:
    """Map BadTransformParam → HTTP 400 with D-07 body (D-08).

    Reads exc.param and exc.value directly — no message-string parsing
    (CLAUDE.md no-magic-strings-in-logic, RESEARCH Pattern 6 alternative).
    """
    return JSONResponse(
        status_code=400,
        content={
            "detail": {
                "error": "bad_request",
                "param": exc.param,
                "value": exc.value,
            }
        },
    )


# ---------------------------------------------------------------------------
# Router registration — image routes LAST (D-01)
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(leagues.router)
app.include_router(images.router)  # LAST — 4-seg and 5-seg image routes (D-01)


def main() -> None:
    """Dev-only entrypoint for the ``api`` project script.

    Production uses the Dockerfile CMD (gunicorn + UvicornWorker).
    """
    import warnings

    warnings.warn(
        "Running via bare uvicorn. For production, use the Dockerfile CMD "
        "(gunicorn + UvicornWorker).",
        stacklevel=2,
    )
    uvicorn.run(
        "matchup_thumbs.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
