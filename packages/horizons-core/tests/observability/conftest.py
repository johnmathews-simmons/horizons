"""Shared fixtures for observability tests.

OTel's global ``TracerProvider`` is set-once: once a real (non-Proxy)
provider is registered, ``trace.set_tracer_provider`` warns and ignores
subsequent calls. Tests work with that constraint by installing a single
SDK ``TracerProvider`` + ``InMemorySpanExporter`` once per module and
clearing the exporter between cases instead of swapping providers.

Auto-uninstrument after each test so the next test starts from a clean
slate (the FastAPI instrumentor mutates app instances per case;
SQLAlchemy and HTTPX flip process-global flags).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def span_exporter() -> InMemorySpanExporter:
    """Install an SDK ``TracerProvider`` with an in-memory exporter once
    per session.

    OTel's ``trace.set_tracer_provider`` is set-once. Module-scoped
    would silently break: the first module's exporter wires up; the
    second module's fixture builds a new ``TracerProvider`` +
    ``InMemorySpanExporter`` but ``set_tracer_provider`` no-ops, leaving
    the second module's tests reading an exporter no spans ever reach.
    Session scope keeps a single exporter wired across all modules;
    the ``_reset_otel_state`` autouse fixture clears it between cases.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture(autouse=True)
def _reset_otel_state(span_exporter: InMemorySpanExporter) -> Iterator[None]:
    """Clear spans before and uninstrument after each test.

    Clearing *before* gives each test a clean canvas even if a previous
    test left spans hanging around (e.g. assertion failure). The post-
    test uninstrument keeps SQLAlchemy / HTTPX from carrying patched
    state between tests; FastAPI instrumentation lives on the per-test
    ``FastAPI`` instance and is GC'd with it.
    """
    span_exporter.clear()
    yield
    if SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry:
        SQLAlchemyInstrumentor().uninstrument()
    if HTTPXClientInstrumentor().is_instrumented_by_opentelemetry:
        HTTPXClientInstrumentor().uninstrument()
