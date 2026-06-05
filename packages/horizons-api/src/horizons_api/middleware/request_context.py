"""Populate observability contextvars for the request lifetime.

Reads ``X-Request-Id`` from the inbound request (or mints a UUID4 when
absent), binds it to ``request_id_var`` for the request lifetime, and
echoes it back on the response so a caller can correlate client-side
logs with our server-side logs.

``user_id_var`` is bound in the auth dependency rather than here: the
binding has to land next to the GUC bracket that sets
``SET LOCAL app.user_id`` so the value RLS sees and the value the log
emits never diverge. The middleware only owns request-id.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from horizons_core.observability.logging import request_id_var
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind ``request_id_var`` for the duration of each request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = request_id
        return response
