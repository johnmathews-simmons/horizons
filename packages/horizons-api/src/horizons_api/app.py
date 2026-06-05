"""FastAPI app factory.

Configures structlog *before* importing FastAPI, mounts the routers,
wires CORS from ``HORIZONS_CORS_ORIGINS``. The ``TokenProvider`` and
``session_for_request`` dependencies are imported transitively when
the routers are mounted; FastAPI builds the dependency graph lazily
on first request.

``create_app`` is a factory so tests can construct an isolated app
per case, override dependencies, and tear it down without process
state leaking between cases.
"""

from __future__ import annotations

# Important — structlog MUST be configured before FastAPI / starlette
# are imported anywhere in the process, because both libraries grab a
# stdlib logger at import time. See logging.py for the trap.
from horizons_api.logging import configure_logging

configure_logging()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from horizons_api.config import load_settings  # noqa: E402
from horizons_api.routes import auth, health, me, primitives, watchlists  # noqa: E402


def create_app() -> FastAPI:
    """Build a fresh ``FastAPI`` instance.

    Settings are read at construction so misconfiguration (missing JWT
    keys, etc.) fails loudly at startup rather than on the first
    authenticated request.
    """
    settings = load_settings()

    app = FastAPI(
        title="Horizons API",
        version="0.0.0",
        description=(
            "Public REST surface for the Horizons regulatory-change intelligence service."
        ),
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(watchlists.router)
    app.include_router(primitives.discovery_router)

    return app
