"""Catch unhandled exceptions inside the CORS middleware boundary.

Starlette's ``Application.build_middleware_stack`` routes any handler
registered as ``@app.exception_handler(Exception)`` (or status 500) to
``ServerErrorMiddleware`` — the **outermost** middleware in the stack,
above ``CORSMiddleware``. The resulting 500 response is therefore
generated *outside* CORS and ships without ``Access-Control-Allow-Origin``.
The browser then reports a CORS error and the actual server-side
exception becomes invisible in the network panel.

This middleware sits one layer inside ``CORSMiddleware``: it catches
``Exception`` from any downstream handler, emits a ``500`` ``JSONResponse``
through the normal ASGI send path, and lets CORS add its headers on the
way back out.

The 2026-06-07 demo-day overview 500 was misdiagnosed as CORS because
the underlying bug (missing ``app_public.change_event_shape()`` SQL
function in the deployed DB) produced this exact pattern. The
regression test lives in ``tests/test_cors.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = structlog.get_logger(__name__)


class CorsFriendlyErrorMiddleware:
    """Convert downstream ``Exception`` into a 500 ``JSONResponse``.

    The conversion happens *inside* the CORS middleware boundary so the
    response carries CORS headers when it reaches the browser. ``HTTP``
    is the only ASGI scope type handled — websocket / lifespan flow
    through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            _logger.exception(
                "unhandled_exception",
                path=scope.get("path"),
                method=scope.get("method"),
            )
            if response_started:
                # Headers already on the wire — can't send a second
                # response. Re-raise so the upstream telemetry layer
                # (``ServerErrorMiddleware``) still records the crash.
                raise
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
            )
            await response(scope, receive, send)
