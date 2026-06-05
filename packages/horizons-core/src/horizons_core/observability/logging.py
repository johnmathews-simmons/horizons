"""structlog configuration with OTEL trace correlation and request context.

A single entry point — ``setup_structlog`` — configures structlog with
the renderer chosen by ``HORIZONS_ENV`` (``prod`` → JSON for log
ingestion; anything else → coloured console for local dev), routes the
stdlib root logger through the same renderer (so FastAPI / uvicorn
access logs join the same stream), and installs processors that surface

- ISO-8601 UTC timestamps
- log level
- the current OTEL ``trace_id`` / ``span_id`` (only when an active span
  exists; absent fields when there isn't one, so log shape stays sane in
  worker / job contexts that aren't inside an HTTP request)
- ``request_id`` and ``user_id`` pulled from this module's context
  variables (populated by middleware in the call-site app — see the
  follow-up wire-up section in ``journal/260605-wu71-...``).

The module is side-effect-free at import time: importing
``horizons_core.observability.logging`` does **not** configure structlog.
The configuration only fires when ``setup_structlog`` is called.

The "canonical trap" of FastAPI / starlette grabbing a stdlib logger at
import time applies to the **call site** (``horizons_api.app``), not to
this module. The call site must invoke ``setup_structlog`` *before* any
``from fastapi import ...`` lands; this module enforces nothing.

``setup_structlog`` is idempotent — repeated calls reconfigure cleanly
without piling up handlers on the root logger. It does not depend on
``setup_otel`` having been called first; if OTel isn't configured, the
trace-correlation processor simply contributes nothing to the event
dict (no ``trace_id`` / ``span_id`` keys).
"""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from typing import Final

import structlog
from opentelemetry import trace

request_id_var: Final[ContextVar[str]] = ContextVar("horizons_request_id", default="")
"""Per-request correlation id. Middleware in the call-site app reads the
inbound ``X-Request-Id`` header (or generates a UUID4 if absent) and
sets this for the request lifetime. Emitted as ``request_id`` in every
log entry produced inside the request."""

user_id_var: Final[ContextVar[str]] = ContextVar("horizons_user_id", default="")
"""Authenticated user id. Set by the auth middleware *after* the
``SET LOCAL app.user_id`` bracket binds the GUC (see the WU1.5 session
contract). Emitted as ``user_id`` in every log entry. Stays empty
string on unauthenticated paths (``/health``, ``/v1/auth/login``)."""


_ENV_VAR: Final[str] = "HORIZONS_ENV"
_PROD_ENV_VALUE: Final[str] = "prod"


def setup_structlog() -> None:
    """Configure structlog + the stdlib root logger.

    Idempotent: clears the root logger's handlers before installing the
    new one and re-runs ``structlog.configure`` to replace any prior
    processor chain.
    """
    renderer = _select_renderer()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_otel_trace_context,
        _add_request_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    _route_stdlib_root_through_structlog(renderer)


def _select_renderer() -> structlog.types.Processor:
    """Pick JSON for prod, coloured console for everything else.

    Default is the console renderer so a forgotten ``HORIZONS_ENV`` in a
    container image is loud (mis-shaped logs on Log Analytics ingest)
    rather than silent (parseable JSON that passes validation but
    confuses the operator about which env is running).
    """
    if os.environ.get(_ENV_VAR) == _PROD_ENV_VALUE:
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=True)


def _route_stdlib_root_through_structlog(
    renderer: structlog.types.Processor,
) -> None:
    """Replace the root logger's handlers with one feeding the renderer.

    FastAPI / uvicorn / starlette log through ``logging.getLogger("...")``
    and inherit the root handler. Without this re-routing their access
    logs would land in plain text alongside our JSON / coloured-console
    structlog stream, and prod log parsers would error on every other
    line.
    """
    formatter = structlog.stdlib.ProcessorFormatter(processor=renderer)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _add_otel_trace_context(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Surface the current OTEL trace / span ids onto every log entry.

    No-ops when no span is current. The check covers two cases: the
    default ``INVALID_SPAN`` returned when the global tracer provider
    isn't configured, and a real span whose ``SpanContext`` is invalid
    (``is_valid == False``). Both should leave the event dict untouched
    so logs from worker / job contexts don't grow phantom zero ids.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return event_dict
    event_dict["trace_id"] = format(span_context.trace_id, "032x")
    event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def _add_request_context(
    _logger: structlog.types.WrappedLogger,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Pull ``request_id`` / ``user_id`` off the module contextvars.

    Only emits the keys when the contextvars are populated (default is
    empty string → omitted). This keeps the schema stable across
    contexts that have no request bound (startup, ingestion worker,
    one-shot migration jobs).
    """
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    user_id = user_id_var.get()
    if user_id:
        event_dict["user_id"] = user_id
    return event_dict
