"""Behavioural tests for ``horizons_core.observability.logging``.

Asserts the public contract:

- ``setup_structlog`` is idempotent and side-effect-free at module
  import time;
- the dev / prod renderer switches on ``HORIZONS_ENV``;
- log entries carry ``timestamp``, ``level``, and the OTEL
  ``trace_id`` / ``span_id`` when an active span exists;
- the ``request_id_var`` and ``user_id_var`` contextvars surface as
  ``request_id`` and ``user_id`` keys in the event dict, and stay out
  of the dict when unset.

The tests rely on the prod (JSON) renderer + stdout capture to inspect
the *post-processor* event dict. ``structlog.testing.capture_logs``
intercepts events *before* the configured processor chain runs, so it
can't see fields our custom processors add.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from typing import TYPE_CHECKING

import pytest
import structlog
from horizons_core.observability.logging import (
    request_id_var,
    setup_structlog,
    user_id_var,
)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

if TYPE_CHECKING:
    from collections.abc import Iterator


def _capture_prod_log(
    monkeypatch: pytest.MonkeyPatch,
    emit: object,
) -> dict[str, object]:
    """Configure prod renderer, run ``emit()``, return the parsed event dict.

    ``emit`` is a no-arg callable that performs the log call(s). Returns
    the last JSON line written to stdout — the renderer emits one line
    per ``log.info(...)`` call.
    """
    monkeypatch.setenv("HORIZONS_ENV", "prod")
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    setup_structlog()
    emit()  # type: ignore[operator]
    output = buffer.getvalue().strip()
    assert output, "no rendered output captured"
    return json.loads(output.splitlines()[-1])


@pytest.fixture(autouse=True)
def _reset_contextvars_and_structlog() -> Iterator[None]:
    """Reset module state between tests.

    ``setup_structlog`` mutates process-global state (root logger,
    structlog config). Each test starts from a clean slate; the
    teardown also resets the contextvars so failures don't poison
    later cases.
    """
    request_id_var.set("")
    user_id_var.set("")
    yield
    structlog.reset_defaults()
    request_id_var.set("")
    user_id_var.set("")


def test_setup_structlog_is_idempotent() -> None:
    """Repeated calls must not pile up handlers on the root logger."""
    setup_structlog()
    setup_structlog()
    setup_structlog()
    assert len(logging.getLogger().handlers) == 1


def test_import_does_not_configure_structlog() -> None:
    """The module is side-effect-free at import time.

    Static check on the AST rather than a live reload: reloading the
    module would re-bind the ``request_id_var`` / ``user_id_var``
    contextvars, breaking the contract the *processors* close over
    (they reference the names in the module's namespace at definition
    time). The static check is the right shape for the contract anyway
    — what matters is that no top-level statement reaches into
    structlog's global config.
    """
    import ast
    import inspect

    import horizons_core.observability.logging as logging_module

    source = inspect.getsource(logging_module)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute):
                pytest.fail(
                    f"module-level call {ast.unparse(node.value)!r} is a side effect at import time"
                )


def test_prod_renderer_emits_json_with_timestamp_and_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``HORIZONS_ENV=prod`` the renderer produces parseable JSON
    carrying ``timestamp`` and ``level``."""
    record = _capture_prod_log(
        monkeypatch,
        lambda: structlog.get_logger("test").info("hello", customer="acme"),
    )
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["customer"] == "acme"
    assert "timestamp" in record


def test_dev_renderer_is_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``HORIZONS_ENV`` unset the console renderer takes over."""
    monkeypatch.delenv("HORIZONS_ENV", raising=False)

    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)

    setup_structlog()
    log = structlog.get_logger("test")
    log.info("hello")

    output = buffer.getvalue().strip()
    assert output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.splitlines()[-1])


def test_request_and_user_context_surface_in_log_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def emit() -> None:
        request_id_var.set("req-abc-123")
        user_id_var.set("user-xyz-456")
        structlog.get_logger("test").info("authorised request")

    record = _capture_prod_log(monkeypatch, emit)
    assert record["request_id"] == "req-abc-123"
    assert record["user_id"] == "user-xyz-456"


def test_context_keys_omitted_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset contextvars must NOT emit empty-string ``request_id`` etc."""
    record = _capture_prod_log(
        monkeypatch,
        lambda: structlog.get_logger("test").info("startup ping"),
    )
    assert "request_id" not in record
    assert "user_id" not in record


def test_otel_trace_context_surfaces_when_span_is_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an OTEL span is active, ``trace_id`` / ``span_id`` are emitted."""
    # Pin a TracerProvider so spans created here have a valid context.
    # OTel's global tracer provider is set-once; if a previous test
    # installed one, reuse it.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())

    tracer = trace.get_tracer(__name__)
    expected: dict[str, str] = {}

    def emit() -> None:
        with tracer.start_as_current_span("unit") as span:
            expected["trace_id"] = format(span.get_span_context().trace_id, "032x")
            expected["span_id"] = format(span.get_span_context().span_id, "016x")
            structlog.get_logger("test").info("inside span")

    record = _capture_prod_log(monkeypatch, emit)
    assert record["trace_id"] == expected["trace_id"]
    assert record["span_id"] == expected["span_id"]


def test_otel_trace_keys_absent_outside_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside an active span the trace keys stay off the event dict."""
    record = _capture_prod_log(
        monkeypatch,
        lambda: structlog.get_logger("test").info("no span"),
    )
    assert "trace_id" not in record
    assert "span_id" not in record


def test_setup_works_without_otel_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """``setup_structlog`` must not require ``setup_otel`` to be called.

    The trace-context processor is defensive: it reads the global
    tracer provider lazily inside the processor call and produces no
    ``trace_id`` / ``span_id`` when the context isn't valid. This
    guards the worker / migration / one-shot script use cases.
    """
    record = _capture_prod_log(
        monkeypatch,
        lambda: structlog.get_logger("test").info("no otel here"),
    )
    # No exception is the contract; we also assert no phantom zero ids.
    assert "trace_id" not in record
