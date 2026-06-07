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
# stdlib logger at import time. See the WU7.1 journal entry for the
# trap; the call site here is what keeps the order correct.
from horizons_core.observability.logging import setup_structlog

setup_structlog()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from horizons_core.observability.otel import setup_otel  # noqa: E402

from horizons_api.admin import audit as admin_audit  # noqa: E402
from horizons_api.admin import clients as admin_clients  # noqa: E402
from horizons_api.admin import health as admin_health  # noqa: E402
from horizons_api.admin import impersonate as admin_impersonate  # noqa: E402
from horizons_api.config import load_settings  # noqa: E402
from horizons_api.middleware import (  # noqa: E402
    CorsFriendlyErrorMiddleware,
    RequestContextMiddleware,
)
from horizons_api.routes import (  # noqa: E402
    admin_subscriptions,
    auth,
    documents,
    health,
    me,
    primitives,
    watchlists,
)


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

    setup_otel(app)

    # Middleware order: Starlette's ``add_middleware`` inserts at
    # position 0, so the *last* call ends up the outermost wrapper.
    # We want — from outside to inside —
    # ``RequestContext → CORS → CorsFriendlyError → router``.
    # CORS must wrap ``CorsFriendlyErrorMiddleware`` so the 500 response
    # the latter emits travels back through CORS and arrives at the
    # browser with ``Access-Control-Allow-Origin`` set; otherwise a
    # server-side bug masquerades as a CORS error and the real
    # exception is invisible in the network panel.
    app.add_middleware(CorsFriendlyErrorMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            # X-Client-Type is the webapp's opt-in on /v1/auth/login for the cookie-shaped response.
            allow_headers=["Authorization", "Content-Type", "X-Client-Type"],
        )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(watchlists.router)
    app.include_router(documents.router)
    app.include_router(primitives.discovery_router)
    app.include_router(primitives.temporal_router)
    app.include_router(primitives.differential_router)
    app.include_router(admin_subscriptions.router)
    app.include_router(admin_clients.router)
    app.include_router(admin_impersonate.router)
    app.include_router(admin_health.router)
    app.include_router(admin_audit.router)

    return app
