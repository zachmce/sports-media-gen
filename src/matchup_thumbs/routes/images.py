"""Image generation routes — 5-seg general form (ROUTE-03 / API-01).

Route hierarchy:
  GET /{sport}/{league}/{away}/{home}/{kind}  — General 5-segment form (D-06)

Handler order of operations (updated for Phase 18 D-06):
  1. Read shared app.state clients.
  2. Bind raw league + kind to structlog contextvars.
  2a. resolve_league(league) → LeagueResolution; None → 404 league_not_found.
  2b. Sport validation: casefold-compare {sport} vs lr.sport;
      mismatch → 404 sport_mismatch.
  2c. Rebind canonical = lr.slug; overwrite raw alias in structlog context.
  3. Resolve away team using canonical slug;
     None → 404 team_not_found(field="away").
  4. Resolve home team using canonical slug;
     None → 404 team_not_found(field="home").
  5. Call render_pipeline with canonical slug → RenderResult;
     UnknownGeneratorError propagates.
  6. Emit per-request metrics and bind cache_tier to structlog contextvars.
  7. Dispatch post_cache_transform via the threadpool (CPU-bound);
     BadTransformParam propagates.
  8. Return Response with CACHE_CONTROL_IMMUTABLE header.

Old 4-segment route /{league}/{away}/{home}/{kind} is removed — natural 404
(ROUTE-05, clean break per Phase 18 D-06, no redirect).

Security
--------
- T-04-03: Path segments are opaque strings passed to parameterised SQL (resolver)
  and the generator registry; no filesystem path is derived from user input.
- T-04-05: ?w uses Query(gt=0) → 422 on non-positive; post_cache_transform handles
  remaining edge-cases (raises BadTransformParam → 400 via main.py handler).
- T-04-06: Metric labels are canonical league/kind/tier only — never raw input.
- T-18-SSRF: Only the canonical slug (KNOWN_LEAGUES-gated by resolve_league)
  reaches resolve() and render_pipeline(); raw path segments do not.
- T-18-CARD: {sport} and raw {league} are never Prometheus label values (D-05).
- T-18-INJ: {sport} is validated via casefold() equality only — never
  interpolated into SQL or a cache key.
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
    CACHE_CONTROL_NO_STORE,
    RenderResult,
    post_cache_transform,
    render_pipeline,
)
from matchup_thumbs.resolver import resolve, resolve_league
from matchup_thumbs.settings import settings

logger = structlog.get_logger()

# Upper bound for the ?fmt query value — the longest supported format ("webp")
# is 4 chars; reject oversized input at the FastAPI validation layer (422)
# before it reaches post_cache_transform (review WR-03).
_FMT_MAX_LEN = 8

router = APIRouter()


# ---------------------------------------------------------------------------
# General 5-segment route  GET /{sport}/{league}/{away}/{home}/{kind}
# ---------------------------------------------------------------------------


@router.get("/{sport}/{league}/{away}/{home}/{kind}")
async def general_image(
    sport: str,
    league: str,
    away: str,
    home: str,
    kind: str,
    request: Request,
    style: Annotated[int, Query()] = 0,
    fmt: Annotated[str, Query(max_length=_FMT_MAX_LEN)] = "png",
    w: Annotated[int | None, Query(gt=0)] = None,
) -> Response:
    """General 5-segment image route for sport-prefixed leagues (ROUTE-03/API-01)."""
    return await _handle_image(request, sport, league, away, home, kind, style, fmt, w)


# ---------------------------------------------------------------------------
# Shared handler body (D-06 order)
# ---------------------------------------------------------------------------


async def _handle_image(
    request: Request,
    sport: str,
    league: str,
    away_input: str,
    home_input: str,
    kind: str,
    style: int,
    fmt: str,
    w: int | None,
) -> Response:
    """Resolve league → validate sport → resolve teams → render → transform → return.

    D-06 order (Phase 18):
      (1)  Read shared app.state clients.
      (2)  Bind raw league + kind to structlog contextvars.
      (2a) resolve_league(league) → lr; None → 404 league_not_found.
      (2b) casefold-compare sport vs lr.sport; mismatch → 404 sport_mismatch.
      (2c) canonical = lr.slug; rebind structlog context to canonical.
      (3)  Resolve away team with canonical slug; miss → 404
           (increments resolution_misses_total).
      (4)  Resolve home team with canonical slug; miss → 404
           (increments resolution_misses_total).
      (5)  Call render_pipeline with canonical slug; record latency + cache_tier metric.
      (6)  Call post_cache_transform via threadpool; BadTransformParam propagates.
      (7)  Return Response with CACHE_CONTROL_IMMUTABLE.

    Raw {league} and {sport} path segments MUST NOT reach resolve()/render_pipeline()/
    metric labels after step (2c) — only the canonical slug flows downstream (T-18-SSRF,
    T-18-CARD, D-05).

    UnknownGeneratorError and BadTransformParam are NOT caught here — they
    propagate to the exception handlers registered in main.py (D-08).
    """
    # (1) Shared clients from app.state (pattern from leagues.py line 35).
    pool = request.app.state.db_pool
    redis = request.app.state.redis
    http_client = request.app.state.http_client

    # (2) Bind raw league + kind first; canonical rebind follows after resolution.
    structlog.contextvars.bind_contextvars(league=league, kind=kind)

    # (2a) Resolve league — league slug or alias → canonical slug + sport.
    lr = await resolve_league(league, pool, redis)
    if lr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "league_not_found", "input": league},
        )

    # (2b) Sport validation — casefold only; {sport} never reaches SQL/cache (T-18-INJ).
    if sport.casefold() != lr.sport.casefold():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "sport_mismatch",
                "sport": sport,
                "league": lr.slug,
                "expected_sport": lr.sport,
            },
        )

    # (2c) Rebind canonical slug — overwrites raw alias in structlog context so
    # logs and downstream metrics always show the canonical form (T-18-CARD / D-05).
    canonical: str = lr.slug
    structlog.contextvars.bind_contextvars(league=canonical)

    # (3) Resolve away team — CANONICAL slug, not raw league (T-18-SSRF).
    resolution_total.labels(league=canonical).inc()
    away_raw = await resolve(canonical, away_input, pool, redis)
    if away_raw is None:
        resolution_misses_total.labels(league=canonical).inc()
        raise HTTPException(
            status_code=404,
            detail={
                "error": "team_not_found",
                "league": canonical,
                "field": "away",
                "input": away_input,
            },
        )
    away: TeamDict = cast(TeamDict, away_raw)

    # (4) Resolve home team — CANONICAL slug (T-18-SSRF).
    resolution_total.labels(league=canonical).inc()
    home_raw = await resolve(canonical, home_input, pool, redis)
    if home_raw is None:
        resolution_misses_total.labels(league=canonical).inc()
        raise HTTPException(
            status_code=404,
            detail={
                "error": "team_not_found",
                "league": canonical,
                "field": "home",
                "input": home_input,
            },
        )
    home: TeamDict = cast(TeamDict, home_raw)

    # (5) Render pipeline — canonical slug → render key is canonical-keyed (D-08).
    # May raise UnknownGeneratorError (handled in main.py).
    t0 = time.perf_counter()
    result: RenderResult = await render_pipeline(
        canonical, away, home, kind, style, redis, http_client, settings, pool
    )
    elapsed = time.perf_counter() - t0

    render_latency_seconds.labels(league=canonical, kind=kind).observe(elapsed)
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

    # (7) Return image response with Cache-Control selected by the kill-switch
    # (CACHE-05 / CACHE-10, D-04): immutable when caching is enabled (default),
    # no-store when disabled so nginx proxy_cache also skips storing the response.
    # The module-level settings singleton is used here (same object passed to
    # render_pipeline at step 5) — no new plumbing required.
    cache_control = (
        CACHE_CONTROL_IMMUTABLE
        if settings.render_cache_enabled
        else CACHE_CONTROL_NO_STORE
    )
    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={"Cache-Control": cache_control},
    )
