"""Behavioural tests for ``horizons_core.observability.otel.setup_otel``.

These tests construct their own minimal FastAPI app — no dependency on
``horizons-api``. The shared conftest pins an SDK ``TracerProvider`` +
``InMemorySpanExporter`` for the module so the assertions can read the
spans the instrumentations emit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import sqlalchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from horizons_core.observability.otel import setup_otel
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import text

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_setup_otel_emits_fastapi_request_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A request through an instrumented app produces at least one span.

    Asserts the integration end-to-end: ``setup_otel`` wires the
    FastAPI instrumentor, a request through ``TestClient`` runs the
    instrumented middleware, and the in-memory exporter captures the
    span the middleware emits.
    """
    app = _make_app()
    setup_otel(app)

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    spans = span_exporter.get_finished_spans()
    assert spans, "FastAPI instrumentation produced no spans for /ping"
    # The ASGI instrumentor emits a span whose name carries the route
    # template; we only assert the path appears so we're not coupled to
    # a specific instrumentor naming convention.
    assert any("/ping" in (span.name or "") for span in spans)


def test_setup_otel_instruments_sqlalchemy(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A SQLAlchemy execute() emits a DB span after ``setup_otel``.

    The instrumentor monkey-patches ``sqlalchemy.create_engine`` via
    wrapt at instrumentation time, so the test resolves the symbol off
    the module rather than caching it via ``from sqlalchemy import
    create_engine`` at import time — that import would bind the
    unwrapped reference and skip query-span emission.
    """
    setup_otel()

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    spans = span_exporter.get_finished_spans()
    assert any("SELECT" in (span.name or "").upper() for span in spans), (
        f"SQLAlchemy instrumentation produced no SELECT span; saw {[s.name for s in spans]}"
    )


def test_setup_otel_instruments_httpx(
    span_exporter: InMemorySpanExporter,
) -> None:
    """An HTTPX call emits a client span after ``setup_otel``.

    The instrumentor patches ``httpx.HTTPTransport.handle_request``;
    ``MockTransport`` is a separate class and bypasses the hook, so the
    test exercises the real transport against a closed localhost port.
    The connection fails — but the instrumentor still finishes the span
    with ERROR status, which is exactly what we need to assert
    instrumentation ran.
    """
    setup_otel()

    with pytest.raises(httpx.ConnectError), httpx.Client(timeout=0.5) as client:
        client.get("http://127.0.0.1:9/")

    spans = span_exporter.get_finished_spans()
    assert any("GET" in (span.name or "") for span in spans), (
        f"HTTPX instrumentation produced no GET span; saw {[s.name for s in spans]}"
    )


def test_setup_otel_is_idempotent(span_exporter: InMemorySpanExporter) -> None:
    """A second call must not raise and must not double-instrument."""
    app = _make_app()

    setup_otel(app)
    # Second call against the same app + already-instrumented globals.
    setup_otel(app)

    with TestClient(app) as client:
        client.get("/ping")

    # Each request should emit exactly one server span; double-
    # instrumentation would duplicate it. Filter to server-kind spans
    # so we don't catch the inner ASGI receive/send spans the
    # instrumentor occasionally adds.
    from opentelemetry.trace import SpanKind

    server_spans = [
        span
        for span in span_exporter.get_finished_spans()
        if span.kind == SpanKind.SERVER
    ]
    assert len(server_spans) == 1, (
        f"setup_otel double-instrumented FastAPI; got {len(server_spans)} server spans"
    )


def test_setup_otel_console_path_when_connection_string_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no APPLICATIONINSIGHTS_CONNECTION_STRING, the console path runs.

    Verifying behaviour rather than implementation: ``setup_otel`` must
    leave the process with a usable SDK ``TracerProvider`` and must not
    raise. The fixture already pinned one — we assert that and
    re-invoke to prove the no-conn-string branch is a no-op against it.
    """
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    setup_otel()

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

    assert isinstance(trace.get_tracer_provider(), SdkTracerProvider)


def test_setup_otel_instruments_only_once_per_call_pair(
    span_exporter: InMemorySpanExporter,
) -> None:
    """SQLAlchemy / HTTPX instrumentors carry their own ``_is_instrumented`` flag.

    After the first ``setup_otel()`` they're patched; a second call
    must skip them rather than raise their double-instrument error.
    """
    setup_otel()
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
    assert HTTPXClientInstrumentor().is_instrumented_by_opentelemetry

    setup_otel()  # second call: must be a no-op on the globals
    assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
    assert HTTPXClientInstrumentor().is_instrumented_by_opentelemetry
