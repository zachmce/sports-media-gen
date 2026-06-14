"""Image generation routes — 4-seg general form and 5-seg NCAA form (API-01, API-02).

Route hierarchy:
  GET /ncaa/{sport}/{away}/{home}/{kind}  — NCAA multi-sport form (D-01, D-02)
  GET /{league}/{away}/{home}/{kind}      — General 4-segment form (D-01)

Handler order of operations per D-05:
  1. Map NCAA sport → canonical league slug (ncaa_image only).
  2. Resolve away team; None → 404 team_not_found(field="away").
  3. Resolve home team; None → 404 team_not_found(field="home").
  4. Call render_pipeline → RenderResult(png, tier); UnknownGeneratorError propagates.
  5. Emit per-request metrics and bind cache_tier to structlog contextvars.
  6. Dispatch post_cache_transform via the threadpool (CPU-bound);
     BadTransformParam propagates.
  7. Return Response with CACHE_CONTROL_IMMUTABLE header.

Security
--------
- T-04-03: Path segments are opaque strings passed to parameterised SQL (resolver)
  and the generator registry; no filesystem path is derived from user input.
- T-04-05: ?w uses Query(gt=0) → 422 on non-positive; post_cache_transform handles
  remaining edge-cases (raises BadTransformParam → 400 via main.py handler).
- T-04-06: Metric labels are league/kind/tier only — never raw away/home/sport input.
- D-03: League validity is delegated entirely to resolve(); no second enum here.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Annotated, cast

import anyio
import structlog
import structlog.contextvars
from fastapi import APIRouter, HTTPException, Query, Request, Response

from matchup_thumbs.generators.types import TeamDict
from matchup_thumbs.metrics import (
    render_cache_events_total,
    render_latency_seconds,
    resolution_misses_total,
    resolution_total,
)
from matchup_thumbs.render import (
    CACHE_CONTROL_IMMUTABLE,
    RenderResult,
    post_cache_transform,
    render_pipeline,
)
from matchup_thumbs.resolver import resolve
from matchup_thumbs.settings import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# NCAA sport → league slug mapping (D-02).
# Sports absent from this map → 404 unknown_sport; no second league enum (D-03).
# ---------------------------------------------------------------------------

NCAA_SPORT_SLUGS: dict[str, str] = {
    "football": "ncaaf",
    "basketball": "ncaab",
}

# Upper bound for the ?fmt query value — the longest supported format ("webp")
# is 4 chars; reject oversized input at the FastAPI validation layer (422)
# before it reaches post_cache_transform (review WR-03).
_FMT_MAX_LEN = 8

router = APIRouter()


# ---------------------------------------------------------------------------
# NCAA 5-segment route  GET /ncaa/{sport}/{away}/{home}/{kind}
# ---------------------------------------------------------------------------


@router.get("/ncaa/{sport}/{away}/{home}/{kind}")
async def ncaa_image(
    sport: str,
    away: str,
    home: str,
    kind: str,
    request: Request,
    style: Annotated[int, Query()] = 0,
    fmt: Annotated[str, Query(max_length=_FMT_MAX_LEN)] = "png",
    w: Annotated[int | None, Query(gt=0)] = None,
) -> Response:
    """NCAA multi-sport image route; maps sport to canonical league slug (API-02)."""
    league = NCAA_SPORT_SLUGS.get(sport)
    if league is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_sport", "sport": sport},
        )
    return await _handle_image(request, league, away, home, kind, style, fmt, w)


# ---------------------------------------------------------------------------
# General 4-segment route  GET /{league}/{away}/{home}/{kind}
# ---------------------------------------------------------------------------


@router.get("/{league}/{away}/{home}/{kind}")
async def general_image(
    league: str,
    away: str,
    home: str,
    kind: str,
    request: Request,
    style: Annotated[int, Query()] = 0,
    fmt: Annotated[str, Query(max_length=_FMT_MAX_LEN)] = "png",
    w: Annotated[int | None, Query(gt=0)] = None,
) -> Response:
    """General 4-segment image route for single-sport leagues (API-01)."""
    return await _handle_image(request, league, away, home, kind, style, fmt, w)


# ---------------------------------------------------------------------------
# Shared handler body (D-05 order)
# ---------------------------------------------------------------------------


async def _handle_image(
    request: Request,
    league: str,
    away_input: str,
    home_input: str,
    kind: str,
    style: int,
    fmt: str,
    w: int | None,
) -> Response:
    """Resolve → render → transform → return image Response.

    D-05 order:
      (1) Read shared app.state clients.
      (2) Bind league + kind to structlog contextvars.
      (3) Resolve away; miss → 404 (increments resolution_misses_total).
      (4) Resolve home; miss → 404 (increments resolution_misses_total).
      (5) Call render_pipeline; record latency + cache_tier metric + bind cache_tier.
      (6) Call post_cache_transform via threadpool; BadTransformParam propagates.
      (7) Return Response with CACHE_CONTROL_IMMUTABLE.

    UnknownGeneratorError and BadTransformParam are NOT caught here — they
    propagate to the exception handlers registered in main.py (D-08).
    """
    # (1) Shared clients from app.state (pattern from leagues.py line 35).
    pool = request.app.state.db_pool
    redis = request.app.state.redis
    http_client = request.app.state.http_client

    # (2) Bind per-request fields; cache_tier added at step (5) after render.
    structlog.contextvars.bind_contextvars(league=league, kind=kind)

    # (3) Resolve away team.
    resolution_total.labels(league=league).inc()
    away_raw = await resolve(league, away_input, pool, redis)
    if away_raw is None:
        resolution_misses_total.labels(league=league).inc()
        raise HTTPException(
            status_code=404,
            detail={
                "error": "team_not_found",
                "league": league,
                "field": "away",
                "input": away_input,
            },
        )
    away: TeamDict = cast(TeamDict, away_raw)

    # (4) Resolve home team.
    resolution_total.labels(league=league).inc()
    home_raw = await resolve(league, home_input, pool, redis)
    if home_raw is None:
        resolution_misses_total.labels(league=league).inc()
        raise HTTPException(
            status_code=404,
            detail={
                "error": "team_not_found",
                "league": league,
                "field": "home",
                "input": home_input,
            },
        )
    home: TeamDict = cast(TeamDict, home_raw)

    # (5) Render pipeline — may raise UnknownGeneratorError (handled in main.py).
    t0 = time.perf_counter()
    result: RenderResult = await render_pipeline(
        league, away, home, kind, style, redis, http_client, settings
    )
    elapsed = time.perf_counter() - t0

    render_latency_seconds.labels(league=league, kind=kind).observe(elapsed)
    render_cache_events_total.labels(tier=result.tier).inc()
    # Bind cache_tier AFTER successful render so a 404 in step (3)/(4) never
    # sets this field (Pitfall 2 / D-13 guard).
    structlog.contextvars.bind_contextvars(cache_tier=result.tier)

    # (6) Post-cache transform via threadpool — CPU-bound (GEN-04 principle).
    # Pitfall 4: use partial with positional args so `requested_w` (not `w`) is matched.
    # BadTransformParam propagates to the exception handler in main.py (D-08).
    image_bytes: bytes
    content_type: str
    image_bytes, content_type = await anyio.to_thread.run_sync(
        partial(post_cache_transform, result.png, kind, fmt, w)
    )

    # (7) Return image response with immutable Cache-Control (CACHE-05).
    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={"Cache-Control": CACHE_CONTROL_IMMUTABLE},
    )
