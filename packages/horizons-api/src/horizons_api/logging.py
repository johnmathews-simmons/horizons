"""structlog configuration.

Called before FastAPI / starlette are imported anywhere in the process
because both libraries grab a stdlib logger at import time. structlog's
ProcessorFormatter retrofits onto the same root logger so the stdlib
log records FastAPI emits go through the same pipeline as the
structlog calls in our code; the canonical trap is configuring
structlog *after* the FastAPI logger has already cached its handler
reference, which leaks plain-text records past the JSON formatter.

WU4.1 keeps this minimal: a JSON renderer in prod, a pretty console
renderer in dev. WU7.1 will add request_id / user_id / trace_id
processors when the OTEL distro lands.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging() -> None:
    """Install a structlog-routed root handler before any other logger.

    Idempotent — repeated calls are no-ops. Decides the renderer from
    ``HORIZONS_LOG_FORMAT`` (``json`` for prod, anything else for the
    dev console renderer); the default is ``json`` so a container image
    without the env var still emits parseable logs.
    """
    fmt = os.environ.get("HORIZONS_LOG_FORMAT", "json").lower()
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
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

    # Route stdlib logger output through the same renderer so FastAPI /
    # uvicorn access logs land in the same JSON stream.
    formatter = structlog.stdlib.ProcessorFormatter(processor=renderer)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
