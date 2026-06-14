"""ASGI request-logging middleware (D-12/D-13/D-14, OBS-02).

Per-request lifecycle:
1. ``clear_contextvars()`` — FIRST: prevents stale context from a prior request
   in the same asyncio task from leaking into the new request's log lines
   (RESEARCH Pitfall 2 — contextvar bleed guard).
2. Read ``X-Request-ID`` header and use it if present; otherwise generate
   ``uuid4().hex``.  Honors upstream / nginx-supplied IDs (D-12).
3. ``bind_contextvars(request_id, method, path)`` — merged into every log line
   in this request via the ``merge_contextvars`` processor already in main.py.
4. Delegate to the inner ASGI stack (Prometheus middleware + route handlers).
5. Capture the HTTP status code and echo ``x-request-id`` on the response.
6. In the ``finally`` block: bind ``status_code`` + ``latency_ms``, emit one
   ``request_completed`` log line (D-13/D-14 — log every request, no sampling).
7. ``clear_contextvars()`` again so the asyncio task's context is clean for any
   subsequent async cleanup or the next sequential request (Pitfall 2).

The route handler is responsible for binding ``league``, ``kind``, and
``cache_tier`` after a successful render (images.py _handle_image).  Those keys
are automatically merged into the final ``request_completed`` line via
``merge_contextvars``.  Non-image routes simply omit those keys.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

import structlog
import structlog.contextvars
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger()

# Inbound X-Request-ID is untrusted. Opaque correlation IDs (uuid hex, uuids
# with dashes, W3C trace IDs) use only [A-Za-z0-9._-]; strip everything else —
# CR/LF, spaces, and other control characters — so a forged header cannot break
# or spoof a structured log line, and bound the length (T-04-10 / review WR-02).
_REQUEST_ID_MAX_LEN = 128
_REQUEST_ID_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_request_id(raw: str | None) -> str:
    """Return a safe correlation ID from an inbound header value.

    Strips disallowed characters and bounds length; falls back to a fresh
    ``uuid4().hex`` when the header is absent or sanitizes to empty.
    """
    if raw is None:
        return uuid.uuid4().hex
    cleaned = _REQUEST_ID_DISALLOWED.sub("", raw)[:_REQUEST_ID_MAX_LEN]
    return cleaned or uuid.uuid4().hex


class RequestLoggingMiddleware:
    """Per-request structlog contextvars binding and completion log line.

    Registered in ``main.py`` via ``app.add_middleware(RequestLoggingMiddleware)``
    AFTER ``instrumentator.instrument(app)`` so that this middleware runs
    outermost — wrapping the Prometheus instrumentation middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # FIRST: clear any stale context from a prior request in this task
        # (Pitfall 2 — prevents league/kind/cache_tier from a prior image
        # request bleeding onto the next request's request_completed line).
        structlog.contextvars.clear_contextvars()

        request = Request(scope)
        request_id = _sanitize_request_id(request.headers.get("X-Request-ID"))
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        t0 = time.perf_counter()
        status_code = 500  # default; overwritten when response.start arrives

        async def send_with_request_id(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Echo the request_id back on the response (D-13).
                # request_id is sanitized to [A-Za-z0-9._-] and length-bounded
                # by _sanitize_request_id, so this echo cannot carry CR/LF or
                # other control characters into the response (T-04-10).
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            structlog.contextvars.bind_contextvars(
                status_code=status_code,
                latency_ms=latency_ms,
            )
            # One completion line per request — no sampling (D-14).
            # league/kind/cache_tier (if bound by the image route handler)
            # are merged in automatically by the merge_contextvars processor.
            await logger.ainfo("request_completed")
            # Clear again so the task's context is clean for cleanup / next req.
            structlog.contextvars.clear_contextvars()
