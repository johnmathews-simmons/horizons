# 2026-06-05 — WU7.0 OpenTelemetry instrumentation

First half of the Track 7 observability spine. Lands an importable,
test-driven `setup_otel()` callable in `horizons-core`. The actual
3-line wire-up into `horizons-api/app.py` is deferred to a follow-up
commit (see "Follow-up wire-up" below) because Track 4's WU4.4
concurrently edits the same `app.py`. Keeping the modules separate
buys merge safety; the wire-up cost is a few lines.

## What shipped

- `packages/horizons-core/src/horizons_core/observability/__init__.py`
  — new empty subpackage marker.
- `packages/horizons-core/src/horizons_core/observability/otel.py` —
  the `setup_otel(app: FastAPI | None = None) -> None` entry point.
  - Reads `APPLICATIONINSIGHTS_CONNECTION_STRING`. If present, the
    `azure-monitor-opentelemetry` distro initialises the tracer
    provider, metrics, and log pipelines; the ACA managed OTEL agent
    picks the traffic up downstream. If absent (local dev, tests), a
    `TracerProvider` with a `ConsoleSpanExporter` is installed instead.
  - If a caller (read: a test) has already installed an SDK
    `TracerProvider`, we leave it alone. OTel's
    `trace.set_tracer_provider` is *set-once*; treating that as an
    invariant rather than fighting it means tests can pin an
    `InMemorySpanExporter` before calling `setup_otel` and the
    instrumentations attach to their provider.
  - `FastAPIInstrumentor.instrument_app(app)` runs only when `app` is
    passed. The FastAPI instrumentor stores its per-app marker
    (`app._is_instrumented_by_opentelemetry`) and silently no-ops on
    repeat calls against the same instance.
  - `SQLAlchemyInstrumentor` and `HTTPXClientInstrumentor` are
    process-global; we gate them on
    `is_instrumented_by_opentelemetry` so repeated `setup_otel()`
    calls don't raise their double-instrument errors.
  - Azure-Monitor and FastAPI-instrumentor imports are deferred to
    the inside of their setup helpers so importing
    `horizons_core.observability.otel` from the worker (no FastAPI
    dep) and tests (no Azure Monitor needed) is light.
- `packages/horizons-core/tests/observability/__init__.py`,
  `conftest.py`, `test_otel.py` — six behavioural tests covering:
  request-span emission via `TestClient`; SQLAlchemy SELECT-span
  emission; HTTPX client-span emission (real `HTTPTransport` against
  a closed localhost port; the connection fails but the span lands);
  the idempotency contract (two `setup_otel(app)` calls yield exactly
  one server span per request); the no-conn-string console path; and
  the SQLAlchemy / HTTPX once-per-process gating.
- `packages/horizons-core/pyproject.toml` — three new runtime deps:
  `azure-monitor-opentelemetry>=1.6`,
  `opentelemetry-instrumentation-httpx>=0.50b0`,
  `opentelemetry-instrumentation-sqlalchemy>=0.50b0`. Plus
  `opentelemetry-instrumentation-fastapi>=0.50b0`, which arrives
  transitively via the Azure-Monitor distro but is declared
  explicitly so the import in `_instrument_fastapi` matches a
  first-class dependency.

## Q1–Q4 decisions

1. **Idempotent + caller-pinned provider over destructive overwrite.**
   The first instinct is `trace.set_tracer_provider(...)` every call,
   but OTel set-once semantics turn that into a no-op on the second
   call and a silent test-vs-prod divergence. Idempotency via
   `isinstance(trace.get_tracer_provider(), SdkTracerProvider)`
   makes tests trivial: they install their `InMemorySpanExporter`
   first, call `setup_otel`, and the instrumentations attach to the
   pre-installed provider. Production callers don't notice — the
   isinstance check fails on the cold-start `ProxyTracerProvider`
   and the conn-string branch runs.

2. **FastAPI instrumentation per-app, SQLAlchemy / HTTPX
   process-global.** Matches how each library's instrumentor works —
   FastAPI's is bound to a specific app instance, the other two are
   global. Don't fight it; mirror the library shapes.

3. **No structlog wiring in this module.** Logging (WU7.1) is a
   separate concern with its own import-ordering trap. Keeping
   `setup_otel` orthogonal lets the worker (no FastAPI, no
   structlog) call it without pulling either dependency.

4. **Lazy imports for Azure-Monitor and FastAPIInstrumentor.** The
   distro is heavy at import time and unit tests skip the prod path;
   the FastAPI instrumentor is only needed when `app` is passed.
   Module import stays cheap; the imports happen only when the
   relevant branch fires.

## Gotchas hit during implementation

1. **SQLAlchemyInstrumentor's `create_engine` wrap doesn't see
   pre-cached references.** First test attempt:
   `from sqlalchemy import create_engine` at module top, then
   `setup_otel()`, then `engine = create_engine(...)` — only the
   `connect` span fired, not the query span. The instrumentor
   `wrapt.wrap_function_wrapper`s `sqlalchemy.create_engine` at
   instrumentation time, but the test's `from sqlalchemy import
   create_engine` captured the unwrapped reference at module-load
   time. Fix: use `sqlalchemy.create_engine(...)` via the module
   attribute so the lookup sees the wrapped binding. Production code
   will get this right by accident — `setup_otel` runs at process
   start, *before* any `core/db/session.py` resolves
   `sqlalchemy.create_engine`. The test docstring captures the trap
   so the next reader doesn't have to discover it.

2. **HTTPX instrumentor's wrap bypasses `httpx.MockTransport`.** The
   instrumentor patches `httpx.HTTPTransport.handle_request` (and
   the async sibling); `MockTransport` is a separate class and the
   patch doesn't reach it. Test rewrote to use the real
   `HTTPTransport` pointed at a closed localhost port — connection
   fails fast, instrumentor emits a span with ERROR status, which is
   exactly the contract under test.

3. **Cross-module test pollution via session-vs-module fixture
   scope.** First conftest pass made the `InMemorySpanExporter`
   module-scoped. Test_logging.py ran first, pinned a provider, ran
   nine tests against its exporter, then test_otel.py started, the
   conftest fixture built a NEW provider + exporter, called
   `trace.set_tracer_provider(...)` — set-once no-op — and all
   test_otel cases read an exporter no spans ever reached. Fix:
   session-scoped exporter so both modules share the same one;
   `_reset_otel_state` clears between cases.

4. **Pyright reports `configure_azure_monitor` as partially unknown.**
   The distro doesn't ship PEP-561 stubs. Single `# pyright: ignore`
   on the import + call rather than blanket-disabling the rule.

5. **Ruff TC003 / TC001 on test imports.** Several test imports were
   for types referenced only inside annotations (e.g.
   `InMemorySpanExporter` as a fixture return type with
   `from __future__ import annotations`). Moved under `TYPE_CHECKING`
   where ruff requested.

## Tests

15 new observability tests passing (6 for OTel, 9 for structlog in
WU7.1). Full suite: 302 passed, 4 skipped (fixture-too-small), 155
deselected (`-m 'not integration'`). Local sweep before merge:
`uv run ruff check .`, `uv run pyright`, `uv run pytest -m "not
integration"`, `uv run pre-commit run --all-files`. All green.

## Follow-up wire-up (deferred to a separate commit after WU4.4 lands)

Track 4 is editing `packages/horizons-api/src/horizons_api/app.py`
this session to register the v1 primitive routers. To avoid a merge
conflict, this branch does not touch `app.py`. After WU4.4 merges,
add the lines below — verbatim — in a small follow-up commit.

The current `app.py` calls a local `configure_logging` at module top
(see `packages/horizons-api/src/horizons_api/logging.py`). The
follow-up commit swaps that for `setup_structlog` (WU7.1) and adds
`setup_otel(app)` inside the factory.

```python
# packages/horizons-api/src/horizons_api/app.py
# -- at module top, BEFORE any fastapi import -----------------
from horizons_core.observability.logging import setup_structlog

setup_structlog()

# -- existing imports continue as today --
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from horizons_api.config import load_settings  # noqa: E402
from horizons_api.routes import auth, health, me, watchlists  # noqa: E402

# -- inside create_app(), AFTER `app = FastAPI(...)` ----------
from horizons_core.observability.otel import setup_otel  # noqa: E402

setup_otel(app)
```

Once the swap is in, the existing
`packages/horizons-api/src/horizons_api/logging.py` becomes dead
code; delete it in the same follow-up commit.

The request-id / user-id middleware lives in WU7.1's journal entry —
that middleware reads the contextvars exposed by
`horizons_core.observability.logging` and belongs in the same
follow-up commit. Order inside `create_app()`:

1. `app = FastAPI(...)`
2. `setup_otel(app)` (so OTel span ids exist before middleware runs)
3. `app.add_middleware(CORSMiddleware, ...)` (existing)
4. `app.add_middleware(RequestContextMiddleware)` (new — see WU7.1
   journal)
5. router registrations (existing)

## Next session candidates

| WU | Title | Notes |
| --- | --- | --- |
| 4.4 | Three API primitives | Concurrent session. Once it merges, do the `app.py` wire-up commit above. |
| 7.2 | Admin `/health/*` endpoints | Depends on WU4.5 + WU7.0. Queries Log Analytics via UAMI with a 60s cache. |
| 7.3 | Azure Monitor alert rules | Depends on WU6.0 + WU7.0. Bicep-provisioned 5xx / p95 / ingestion alerts. |

WU7.0 closes the trace-emission story for application code paths.
WU7.1 (next journal entry) closes the log-shape story.
