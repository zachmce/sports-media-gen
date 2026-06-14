"""Health probe routes."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from matchup_thumbs.settings import settings

router = APIRouter()

logger = structlog.get_logger()


@router.get("/healthz")
async def liveness() -> JSONResponse:
    """Liveness probe — always 200 if the process is alive."""
    return JSONResponse({"status": "ok"})


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe — 200 only when Postgres AND Redis are reachable (D-15, API-06).

    Runs a lightweight ``SELECT 1`` against Postgres and a ``PING`` against
    Redis concurrently via ``asyncio.gather``.  Each check is individually
    bounded by the named ``settings.readyz_check_timeout`` — no unbounded
    check, no magic number (CLAUDE.md no-magic-numbers).

    Returns:
        200 ``{"status": "ready"}`` when both dependencies are up.
        503 ``{"status": "not_ready", "postgres": <bool>, "redis": <bool>}``
        when either is down, naming the failing side.
    """
    pool = request.app.state.db_pool
    redis_client = request.app.state.redis

    async def check_postgres() -> bool:
        try:
            async with pool.connection(
                timeout=settings.readyz_check_timeout
            ) as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1")
            return True
        except Exception as exc:
            await logger.awarning("readyz_postgres_check_failed", error=str(exc))
            return False

    async def check_redis() -> bool:
        try:
            await asyncio.wait_for(
                redis_client.ping(), timeout=settings.readyz_check_timeout
            )
            return True
        except Exception as exc:
            await logger.awarning("readyz_redis_check_failed", error=str(exc))
            return False

    pg_ok, redis_ok = await asyncio.gather(check_postgres(), check_redis())

    if pg_ok and redis_ok:
        return JSONResponse({"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "postgres": pg_ok, "redis": redis_ok},
    )
