"""OpenTelemetry initialisation for the Horizons API and worker.

A single entry point — ``setup_otel`` — picks the right exporter based on
``APPLICATIONINSIGHTS_CONNECTION_STRING`` (present → Azure Monitor distro
sends to App Insights via the ACA managed OTEL agent; absent → console
exporter for local dev), then applies FastAPI, SQLAlchemy, and HTTPX
auto-instrumentation.

The function is idempotent: callable from process startup and from
per-test fixtures without double-instrumenting and without overwriting a
caller-installed ``TracerProvider``. This matters because OTel's global
tracer provider is set-once, and tests want to install their own SDK
``TracerProvider`` with an ``InMemorySpanExporter`` *before* asking
``setup_otel`` to attach the instrumentations.

The function does *not* configure structlog. Logging lives in
``horizons_core.observability.logging`` and must be configured *first* —
see that module's docstring for the FastAPI import-ordering trap.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider as _SdkTracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


_CONNECTION_STRING_ENV_VAR: Final[str] = "APPLICATIONINSIGHTS_CONNECTION_STRING"


def setup_otel(app: FastAPI | None = None) -> None:
    """Initialise OpenTelemetry and attach FastAPI / SQLAlchemy / HTTPX instrumentation.

    Behaviour:

    - If ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, the
      ``azure-monitor-opentelemetry`` distro is initialised, which wires
      traces / metrics / logs to App Insights through the ACA managed
      OTEL agent.
    - If the env var is unset (local dev, tests), a console-exporting
      SDK ``TracerProvider`` is installed.
    - If a caller has *already* installed an SDK ``TracerProvider``
      (e.g. a test pinning an ``InMemorySpanExporter``), it is left
      alone — the function only attaches the instrumentations.

    FastAPI instrumentation is per-app and only attaches when ``app`` is
    passed. SQLAlchemy and HTTPX instrumentation are process-global; the
    instrumentors themselves no-op on the second call, so this function
    is safe to call more than once per process.
    """
    _ensure_tracer_provider()

    if app is not None:
        # The FastAPI instrumentor stores its own per-app marker
        # (``_is_instrumented_by_opentelemetry`` on the app) and silently
        # no-ops on the second call against the same app.
        _instrument_fastapi(app)

    if not SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry:
        SQLAlchemyInstrumentor().instrument()
    if not HTTPXClientInstrumentor().is_instrumented_by_opentelemetry:
        HTTPXClientInstrumentor().instrument()


def _ensure_tracer_provider() -> None:
    """Install a tracer provider if none is already configured.

    The OTel API ships a ``ProxyTracerProvider`` as the unconfigured
    default. Real ones inherit from the SDK ``TracerProvider``; checking
    isinstance is the documented way to detect whether the caller (or a
    test) has already pinned one. ``trace.set_tracer_provider`` is
    set-once — calling it twice logs a warning and is ignored — so we
    must not call it when the caller has already done so.
    """
    if isinstance(trace.get_tracer_provider(), _SdkTracerProvider):
        return

    conn_str = os.environ.get(_CONNECTION_STRING_ENV_VAR)
    if conn_str:
        _configure_azure_monitor()
    else:
        _configure_console_provider()


def _configure_azure_monitor() -> None:
    """Initialise the Azure Monitor distro.

    Imported lazily so unit tests that don't exercise the prod path
    aren't taxed for the dependency-heavy distro at import time. The
    distro doesn't ship PEP-561 stubs, so pyright sees its function
    type as partially unknown — silence that one warning here rather
    than disable strict type-checking project-wide.
    """
    from azure.monitor.opentelemetry import (
        configure_azure_monitor,  # pyright: ignore[reportUnknownVariableType]
    )

    configure_azure_monitor()  # pyright: ignore[reportUnknownMemberType]


def _configure_console_provider() -> None:
    provider = _SdkTracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def _instrument_fastapi(app: FastAPI) -> None:
    """Attach the FastAPI instrumentor to ``app``.

    Imported lazily so importing this module does not pull
    ``fastapi`` into ``horizons-core``'s import graph for callers that
    only want the SDK / SQLAlchemy / HTTPX wiring (e.g. the ingestion
    worker, which has no FastAPI dependency).
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
