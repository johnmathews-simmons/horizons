# 2026-06-05 — WU7.1 structlog setup with OTEL + request context

*Last revised: 2026-06-05.*
*Path: journal/260605-wu71-structlog-setup.md.*

Second half of the Track 7 observability spine, paired with WU7.0.
Lands `setup_structlog()` plus the two contextvars that future
middleware will populate. Like WU7.0, the wire-up into
`horizons-api/app.py` is deferred to a follow-up commit so this
session doesn't collide with Track 4's WU4.4 edits to the same file.

## What shipped

- `packages/horizons-core/src/horizons_core/observability/logging.py`
  — new module exposing three public names:
  - `setup_structlog() -> None` — idempotent. Picks the renderer from
    `HORIZONS_ENV` (`prod` → JSON for log ingestion; anything else →
    coloured `ConsoleRenderer` for local dev), configures structlog,
    routes the stdlib root logger through the same `ProcessorFormatter`
    (so FastAPI / uvicorn / starlette access logs join the same
    stream). Clears existing root handlers on every call so repeat
    invocations don't pile them up.
  - `request_id_var: ContextVar[str]` — empty default. The
    follow-up middleware reads the inbound `X-Request-Id` header (or
    generates a UUID4) and `set`s this for the request lifetime.
  - `user_id_var: ContextVar[str]` — empty default. The follow-up
    middleware `set`s this after the WU1.5 session bracket binds
    `SET LOCAL app.user_id`, so the value the GUC sees and the
    value the log emits stay in lock-step.
- Processor chain ordered as: `merge_contextvars` →
  `add_log_level` → `TimeStamper(iso, utc)` →
  `_add_otel_trace_context` → `_add_request_context` →
  `StackInfoRenderer` → `format_exc_info` → renderer. The OTEL
  context processor reads `trace.get_current_span()` lazily inside
  the call; absent / invalid span context leaves `trace_id` and
  `span_id` off the entry rather than emitting phantom zero ids.
  The request-context processor reads the two contextvars; empty
  string (the default) → keys omitted, so background contexts
  (worker, migration runner) don't grow vestigial `request_id: ""`
  fields.
- `packages/horizons-core/pyproject.toml` — adds `structlog>=24.4`
  as a runtime dep of the core package. (The same dep was already
  on `horizons-api`; the follow-up commit drops it from
  `horizons-api/pyproject.toml` since the API will import structlog
  transitively via core's `setup_structlog`.)
- `packages/horizons-core/tests/observability/test_logging.py` —
  nine behavioural tests:
  - idempotency (root has exactly one handler after N calls);
  - import is side-effect-free (AST walk of the module — no
    top-level `Attribute(...)` calls);
  - prod renderer emits parseable JSON with `event`, `level`,
    `timestamp`;
  - dev renderer does NOT emit JSON (the negative test for the
    `HORIZONS_ENV != "prod"` branch);
  - `request_id_var` / `user_id_var` surface as `request_id` /
    `user_id` keys when set;
  - both keys are omitted when the contextvars are at their
    empty-string default;
  - OTEL `trace_id` / `span_id` surface when an active span exists;
  - both trace keys are absent when no span is current;
  - `setup_structlog` works without `setup_otel` having been called
    (the worker / migration code path).

## Q1–Q4 decisions

1. **Side-effect-free module + idempotent function.** The "canonical
   trap" of FastAPI / starlette caching a stdlib logger reference at
   import time is a *call-site* problem, not a *module-loading*
   problem. Importing `horizons_core.observability.logging` cannot
   reach into structlog config; only `setup_structlog()` does. This
   makes the module safe to import from anywhere (test fixtures,
   middleware modules, the worker) without worrying about ordering.
   The trap moves up the stack to `horizons_api/app.py`, which must
   call `setup_structlog()` before its `from fastapi import ...`.
   The wire-up snippet at the bottom of this entry has it in the
   right place.

2. **Empty-string default, processor omits empty.** Originally
   considered `None` defaults so unset contextvars could be detected
   distinctly. Settled on empty strings because the processor only
   ever checks "set or not", `None` would need extra `# type:
   ignore` at the contextvar declaration (`ContextVar[str | None]`),
   and the empty-string convention is what the existing
   `horizons-api/src/horizons_api/config.py` uses for the same
   shape. Consistency over slight type-purity.

3. **OTEL bridge by hand, not the structlog-otel-bridge package.**
   structlog-otel-bridge is a third-party add-on with thin
   maintenance. The custom processor is six lines; reading the
   current span via the OTel API is the documented interface and
   doesn't need a wrapper.

4. **Stdout via `PrintLoggerFactory(file=sys.stdout)` rather than
   `LoggerFactory()`.** The stdlib bridge handles FastAPI / uvicorn
   logs; structlog calls take the print-logger path so the JSON
   line writes directly to stdout. Mixed pipelines aren't strictly
   necessary, but the existing `horizons-api/logging.py` (to be
   replaced) used the same shape; the new module preserves it so
   the swap-in is invisible at the wire.

## Gotchas hit during implementation

1. **`structlog.testing.capture_logs` short-circuits the configured
   processor chain.** First test pass: `capture_logs()` was used to
   read each entry's event dict and assert `request_id` / `user_id`
   / `trace_id` keys. Every assertion failed because `capture_logs`
   replaces the processor list wholesale with its own capture
   processor — our `_add_request_context` and
   `_add_otel_trace_context` never ran. Rewrote the tests around
   the prod (JSON) renderer + stdout capture via
   `monkeypatch.setattr(sys, "stdout", io.StringIO())`. The single
   capture helper `_capture_prod_log(monkeypatch, emit)` keeps each
   test body to two or three lines.

2. **`importlib.reload()` breaks contextvar identity.** A first
   attempt at `test_import_does_not_configure_structlog` did
   `structlog.reset_defaults(); importlib.reload(logging_module);
   assert not structlog.is_configured()`. The reload re-binds
   `request_id_var` and `user_id_var` to fresh `ContextVar`
   instances inside the reloaded module — but the test file
   imported them at the top under the *old* identity, and the
   reloaded module's processors close over the *new* identity. The
   next test that set the old contextvar saw the value vanish from
   the log entry. Replaced the reload-based check with an AST walk
   of the module source asserting no module-level
   `Attribute(...)` call (i.e. no `structlog.configure(...)` at
   import time). Static check is the right shape for the contract
   anyway.

3. **Cross-module test pollution — session-scoped OTel exporter.**
   The conftest fixture was initially module-scoped; test_logging
   ran first, installed a `TracerProvider` + `InMemorySpanExporter`,
   then test_otel.py's fixture built a new exporter but
   `trace.set_tracer_provider` is set-once and the new exporter
   never received any spans. Fix lives in WU7.0's
   `tests/observability/conftest.py` — session-scoped exporter
   shared across modules — but it bit both halves of the work, so
   it's logged here too.

4. **`cache_logger_on_first_use=True` interacts with the prod /
   dev renderer switch in tests.** Initially seemed worth flipping
   to `False` for test safety, but the proxy returned by
   `structlog.get_logger(name)` is fresh per call and the cached
   `BoundLogger` lives on the proxy instance, not globally — each
   test's `get_logger("test")` builds a logger against the
   current config. So `cache_logger_on_first_use=True` is fine.
   What did need cleanup: an autouse fixture that calls
   `structlog.reset_defaults()` between tests and resets the two
   contextvars to empty so a previous test's value can't leak.

## Tests

9 new behavioural tests, all passing. Combined with WU7.0 the
observability suite is 15 tests, all green. Full suite:
302 passed / 4 skipped / 155 deselected. Local sweep before
merge: `uv run ruff check .`, `uv run pyright`, `uv run pytest -m
"not integration"`, `uv run pre-commit run --all-files` — all
green.

## Follow-up wire-up (deferred until WU4.4 merges)

The wire-up touches `packages/horizons-api/src/horizons_api/app.py`,
which Track 4's WU4.4 is editing this session. After WU4.4 merges,
apply the snippet below in a small follow-up commit alongside the
WU7.0 wire-up. Same commit; both pieces are trivial separately and
the OTel + structlog pair makes one logical change.

### 1. Replace the existing `configure_logging()` call

The current `app.py` opens with:

```python
from horizons_api.logging import configure_logging

configure_logging()

from fastapi import FastAPI  # noqa: E402
# … existing imports
```

Replace with:

```python
# packages/horizons-api/src/horizons_api/app.py
# -- at module top, BEFORE any fastapi import ---------------------
from horizons_core.observability.logging import setup_structlog

setup_structlog()

from fastapi import FastAPI  # noqa: E402
# … existing imports
```

Then delete `packages/horizons-api/src/horizons_api/logging.py`
(the local `configure_logging` becomes dead code) and drop
`structlog>=24.4` from `packages/horizons-api/pyproject.toml`
(`horizons-core` brings it in transitively).

### 2. Wire `setup_otel(app)` inside the app factory

Inside `create_app()`, after `app = FastAPI(...)`:

```python
from horizons_core.observability.otel import setup_otel  # noqa: E402

def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Horizons API", …)

    setup_otel(app)  # <-- new line, before middleware so spans wrap them

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, …)

    app.add_middleware(RequestContextMiddleware)  # <-- new, see §3

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(watchlists.router)
    return app
```

### 3. Add a request-context middleware

Create `packages/horizons-api/src/horizons_api/middleware/request_context.py`:

```python
# packages/horizons-api/src/horizons_api/middleware/request_context.py
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from horizons_core.observability.logging import request_id_var, user_id_var


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Populate request_id_var and (when an authenticated principal is
    on the request) user_id_var for the request lifetime.

    Order matters: this middleware must run AFTER auth has resolved
    the principal but BEFORE any route handler logs. With Starlette
    BaseHTTPMiddleware that's just registration order — auth as a
    dependency runs inside the handler, so user_id is set lazily by
    the dependency itself. For request_id, this middleware is the
    right place.
    """

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
```

For `user_id_var`, the cleanest place to bind it is in the
existing auth dependency (`horizons_api/deps/auth.py` or
equivalent) right after the principal is resolved:

```python
# inside the auth Depends() function, after we know the user_id:
from horizons_core.observability.logging import user_id_var

user_id_var.set(str(claims["sub"]))
# (no reset — contextvars are per-request thanks to asyncio task
# locals; FastAPI runs each request in a fresh task.)
```

That keeps the binding next to the GUC bracket (which also sets
`SET LOCAL app.user_id`), so the value the database row-level
security sees and the value the log entry emits never diverge.

### 4. Sanity check after wire-up

After merging, hit `/v1/health` once locally and confirm:

- stdout shows JSON with `event`, `level`, `timestamp`,
  `trace_id`, `span_id`, `request_id`;
- a second request with `-H 'x-request-id: abc'` echoes the same
  `request_id` in the log and in the response header.

## Next session candidates

| WU | Title | Notes |
| --- | --- | --- |
| 4.4 → wire-up | App.py wire-up | Apply the snippets above once WU4.4 merges. |
| 7.2 | Admin `/health/*` endpoints | Depends on WU4.5 + WU7.0. |
| 7.3 | Azure Monitor alert rules | Depends on WU6.0 + WU7.0. |
| 7.4 | Admin audit log surface | Depends on WU1.9 + WU4.5. |

Track 7's foundations land here. The remaining track-7 work (admin
health, alerts, audit) all depend on Track 4 advancing further
before they're useful.
