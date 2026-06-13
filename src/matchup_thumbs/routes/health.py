"""Health probe routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def liveness() -> JSONResponse:
    """Liveness probe — always 200 if the process is alive."""
    return JSONResponse({"status": "ok"})
