# 2026-06-05 — WU4.1: FastAPI app shell + auth middleware

Second unit of this session. Sits on top of WU4.0's `TokenProvider`
seam — the auth dependency calls `verify_token` and the
session-per-request bracket inherits the WU1.5 transaction shape so
RLS fires end-to-end.

## What shipped

### App shell (`horizons_api`)

- `app.py` — `create_app()` factory. structlog is configured at the
  **top** of the module, before FastAPI / starlette imports, because
  both libraries grab a stdlib logger at import time (the WU7.1 trap
  documented in the plan). CORS middleware is wired only when
  `HORIZONS_CORS_ORIGINS` is set; allow-credentials + a fixed method
  / header allow-list. Settings are loaded at construction so missing
  env vars fail loudly on startup instead of on the first
  authenticated request.

- `logging.py` — minimal structlog config. JSON renderer in prod
  (default), ConsoleRenderer in dev when `HORIZONS_LOG_FORMAT≠json`.
  Idempotent — repeated calls re-install the same handler. WU7.1
  will refine this with request_id / user_id / trace_id processors
  when the OTEL distro lands.

- `config.py` — `ApiSettings` (frozen dataclass) + `load_settings()`
  reads the env once. The four JWT env vars are `_require_env`'d (a
  loud `RuntimeError` on missing) so silent fallback to weak defaults
  is structurally impossible; `HORIZONS_CORS_ORIGINS` is optional and
  parsed comma-separated.

### Dependencies (`horizons_api.deps`)

- `provider.py` — `get_token_provider()` lazy-builds a singleton
  `LocalJwtProvider` from `ApiSettings`. `reset_provider_for_tests()`
  exists for integration tests that mutate the env between cases;
  production never calls it.

- `auth.py` — `authenticated_user` extracts `Authorization: Bearer`
  via `HTTPBearer(auto_error=False)`, calls
  `TokenProvider.verify_token`, returns a `Principal`. Missing OR
  malformed OR invalid → `HTTPException(401)` with
  `WWW-Authenticate: Bearer`. The body intentionally does not
  distinguish the three cases — the verifier's specific reason is
  logged for operations but never echoed to clients.

- `session.py` — `session_for_request` depends on
  `authenticated_user`, opens a `get_session(principal.user_id)`
  bracket, calls `set_local_role(session, "api_app")`, yields the
  session. The bracket commits / rolls back per the WU1.5 contract.

### Routes (`horizons_api.routes`)

- `health.py` — `GET /healthz` returns `{"status": "ok"}` with no
  DB hit. Liveness probe semantics: process up + HTTP serving;
  readiness checks that *do* round-trip the DB will sit on a
  different path so ACA can scale the two probes independently.

- `me.py` — `GET /v1/me` returns a frozen `MeResponse` echoing the
  decoded `Principal` (`user_id`, `role`, `kind`). Deliberate WU4.1
  stub; the real implementation in WU4.3 will fetch the user row +
  subscription summary through the repository layer and add
  `Cache-Control: private, no-store`. The path stays stable so the
  webapp can wire to it now without a follow-up rename.

### Tests (+7)

`packages/horizons-api/tests/test_app_auth.py` — 7 sync tests over
`fastapi.testclient.TestClient`:

- `/healthz` returns 200 unauthenticated, **with `HORIZONS_DB_URL`
  deliberately deleted** to prove the route's dependency tree doesn't
  open an engine lazily.
- Missing bearer → 401 (with `WWW-Authenticate: Bearer`).
- Malformed Authorization header (`Basic ...`) → 401 (not 422).
- Invalid bearer string → 401.
- Token signed by a different keypair → 401 (the signature-failure
  branch of `InvalidTokenError`).
- Valid bearer (issued by the same provider the app uses) → 200 with
  `{user_id, role, kind}` body matching the principal.
- Missing required JWT env var → `RuntimeError` at `create_app()`,
  not a deferred crash on first request.

The test fixture wires an ephemeral RSA keypair into the env, calls
`reset_provider_for_tests()` so each case rebuilds the singleton, and
uses `TestClient` as a context manager so the startup / shutdown
lifecycle runs.

### Doc / config touches

- `pyproject.toml` (workspace root) — per-file-ignores for
  `packages/horizons-api/src/horizons_api/deps/*.py` and
  `routes/*.py` carry `TC001`, `TC002`, `TC003`. Same shape as the
  Pydantic / SQLAlchemy carve-outs, same reason: FastAPI's
  dependency injection resolves parameter annotations via
  `typing.get_type_hints()` at app-construction time, so every type
  referenced in an `Annotated[T, Depends(...)]` parameter must be
  importable at runtime — `TYPE_CHECKING`-only imports break it.

- `packages/horizons-api/pyproject.toml` — runtime deps:
  `fastapi>=0.115`, `structlog>=24.4`, `uvicorn>=0.32`.

- Workspace `pyproject.toml` dev group gains `httpx>=0.27` for the
  `TestClient`'s transport.

## Open questions resolved this session

Four pre-edit decisions:

1. **Where the auth deps live.** `horizons_api.deps.{provider,auth,
   session}` rather than a single `dependencies.py`. The three deps
   layer on top of each other (`session_for_request` depends on
   `authenticated_user` depends on `get_token_provider`), and each
   file is small enough to keep the import surface tight. The package
   `__init__.py` re-exports the three names so callers can write
   `from horizons_api.deps import authenticated_user`.

2. **Provider as a process-wide singleton, not a per-request build.**
   `LocalJwtProvider` is stateless once constructed (the keys + issuer
   / audience are immutable); rebuilding it per request would parse
   the PEM every call. `reset_provider_for_tests()` exists only for
   the test fixture and is documented as such.

3. **HTTPBearer auto-error off.** FastAPI's default is `auto_error=
   True` which raises `403` (not `401`) and a starlette-default body.
   The acceptance contract says `401` and the body must not
   distinguish missing / invalid (no internal leakage), so the
   dependency raises `HTTPException(401)` itself with a uniform
   detail message.

4. **Stub `/v1/me` is the test target.** The plan's acceptance lists
   "stub /v1/me" explicitly. Real implementation lands in WU4.3 with
   the repository layer + subscription summary; bringing it forward
   would conflate two units and bake decisions (cache headers,
   subscription DTO shape) that belong to WU4.3.

## Gotcha hit during implementation

**FastAPI dependency parameter annotations must be runtime-importable.**
First version put `TokenProvider` and `Principal` under `TYPE_CHECKING`
in `deps/auth.py` and `deps/session.py` (the TC001/TC002 ruff rules
push you that way for "annotation-only" imports). Result: FastAPI's
`get_type_hints` couldn't resolve the forward-ref string, fell back to
treating the parameter as a regular query parameter, and the test for
"missing bearer → 401" came back with 422 +
`{"detail":[{"type":"missing","loc":["query","provider"],...}]}`.

Fix: import them at module level; add the `TC001` / `TC002` / `TC003`
ignores to `pyproject.toml` per-file-ignores for `deps/*.py` and
`routes/*.py`. The reason is documented inline in the
per-file-ignores block so the next reader doesn't move them back
under `TYPE_CHECKING` for "cleanliness". Same shape as the
Pydantic-runtime-hint carve-out for `repos/*.py` from WU1.6.

**`sqlalchemy.text()` is banned outside `db/session.py`.** First
version of `deps/session.py` called `session.execute(sqlalchemy.
text("SET LOCAL ROLE api_app"))` directly. The architectural test
`tests/test_raw_sql_isolation.py` (WU1.5) failed. Fix: use the
existing `set_local_role(session, "api_app")` helper from
`horizons_core.db.session` — it was added in WU1.9 for the admin
context managers and the allow-list (`{"admin_bypass", "api_app"}`)
already covered `api_app`. The carve-out stays single-file.

## Status by suite (end of WU4.1)

- 368 default-marker tests passing (was 361 → +7 API tests).
- ruff check / ruff format: clean.
- pyright strict: 0 errors (17 third-party stub warnings unchanged).
- pre-commit all-files: clean.
- Webapp gate (lint:check / build / vitest): clean.

## Track 4 status

| WU | Status |
| --- | --- |
| WU4.0 | shipped (`core/auth/{provider,local_jwt,passwords}.py` + `refresh_tokens` table) |
| **WU4.1** | **shipped (`api/app.py`, `deps/*`, `routes/health.py`, `routes/me.py`)** |
| WU4.2 | next — `/v1/auth/{login,refresh,logout}` |
| WU4.3 | depends on WU1.6 (repos exist) — `/v1/me` real implementation + watchlists CRUD |
| WU4.4 | depends on WU3.4 + WU4.1 — `/v1/discovery`, `/v1/temporal`, `/v1/differential` |

The session-per-request bracket (`session_for_request`) is the
canonical Track-4 entry into the WU1.5 RLS spine. Every authenticated
route that touches Postgres adds it as a `Depends(session_for_request)`
parameter; routes that don't need the DB (`/healthz`, future
introspection endpoints) skip it and never open a transaction.

## Cadence note

Worktree-driven flow: `EnterWorktree wu4.0-4.1-auth-and-shell` →
WU4.0 commit + journal commit → WU4.1 code + tests + journal in the
same worktree. The merge cadence per `CLAUDE.md` is push for early
CI signal, then ff-merge into main from the main checkout, push
main, delete remote feature branch, `ExitWorktree(remove)`.
