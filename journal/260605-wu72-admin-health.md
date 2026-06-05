# 2026-06-05 — WU7.2 admin health endpoints

Adds the three admin-only health endpoints required by the
locked-in observability posture (improvement-plan §12): a per-window
Application Insights view of the API, an ingestion-side queue +
incident view, and a Postgres connection + slow-query view.

## What shipped

### `core/observability/health.py`

- `fetch_api_metrics(window)` — single KQL projection over the
  `AppRequests` workspace table returning `(rate_per_minute, p95_ms,
  error_rate)` for either `"1h"` or `"24h"`. Cached in a process-wide
  `cachetools.TTLCache` keyed by `(query_id, window)` with a 60 s TTL.
- `HealthQueryUnavailable` carries a one-line `reason` the route
  surfaces verbatim. Raised on:
  - missing `HORIZONS_LOG_ANALYTICS_WORKSPACE_ID` (local-dev default);
  - `azure-identity` / `azure-monitor-query` import failure;
  - `DefaultAzureCredential` initialisation failure;
  - any exception from `LogsQueryClient.query_workspace`.
- `set_logs_query_client_for_tests` / `reset_logs_query_client_for_tests`
  are the test seam. The default client is lazy — built on first
  call — so a test that overrides the seam pays no Azure-SDK import
  cost.

### `admin/health.py`

Three GET routes under `/v1/admin/health`:

- `GET /api` — wraps `fetch_api_metrics("1h")` and `("24h")` into a
  response with a stable `data_source` discriminator per window. When
  the underlying call raises `HealthQueryUnavailable`, the window slot
  returns `{"data_source": "unavailable", "reason": "...",
  "values": null}` instead of 500-ing.
- `GET /ingestion` — counts overdue rows in `document_poll_schedule`
  (`next_poll_at < now()`), samples the oldest 20, and lists the last
  24 h of `ingestion_incident` rows (capped at 50, newest first).
- `GET /db` — connection count from `pg_stat_activity`, replication
  lag pinned to `null` (no replica until post-demo), top-5 slow
  queries from `pg_stat_statements` when the extension is installed
  or the `unavailable` envelope when not.

All three depend on the WU4.5 admin dep stack
(`require_admin_principal` + `admin_operator_session_for_request`),
so each request writes one `admin_access_log` row before the route
body executes — same semantics as the WU4.5 subscription endpoints.

Raw-SQL discipline: every DB read is built from
`sqlalchemy.table()` / `column()` expressions so the `text()` ban
in `tests/test_raw_sql_isolation.py` continues to hold. The
`document_poll_schedule` and `ingestion_incident` table descriptors
live at module top; pg_stat_activity / pg_extension /
pg_stat_statements use the same `table()` form for the same reason.

### Migration `0012_admin_bypass_ingestion_reads.py`

Grants `admin_bypass` read-only `SELECT` on `document_poll_schedule`
and `ingestion_incident`. WU3.1 created the tables with
`ingestion_worker`-only grants; `BYPASSRLS` doesn't override
table-level grants, so the health endpoint would otherwise fail with
`permission denied`. Read-only on purpose — admin operators inspect
the queues but never mutate cadence or write incident rows.

The existing
`tests/test_ingestion_tables_migration.py::test_per_role_grants_match_design`
asserted "no admin_bypass grants on either table" — relaxed to
"admin_bypass has exactly `{SELECT}` on each" with an inline comment
pointing at WU7.2's reason. Write privileges remain unchanged: no
INSERT / UPDATE / DELETE for admin_bypass on either table.

### Dependencies

`packages/horizons-core/pyproject.toml` gains three runtime deps:

- `azure-identity>=1.19` — `DefaultAzureCredential` for the
  managed-identity → workspace auth path.
- `azure-monitor-query>=1.4` — `LogsQueryClient.query_workspace`.
- `cachetools>=5.5` — `TTLCache` for the 60 s in-process cache.

All three are deferred imports (or test-overridable seams) so the
worker / migration runner / tests that don't touch the surface pay
no extra import cost.

## Tests (`tests/api/`)

`tests/api/conftest.py` mirrors the WU4.5 subscription test harness
(session-scoped migrated Postgres, per-test RSA keypair, configured
env with the Log Analytics workspace id deliberately *unset* unless
a specific test overrides it). Two helpers — `make_user`, `login`,
`bearer` — keep the per-test boilerplate small.

`tests/api/test_admin_health_endpoints.py` — 7 integration tests:

- `/api` returns the unavailable shape when the workspace env is
  absent (the local-dev default).
- `/api` returns the metric shape when a stub
  `_LogsQueryClientProtocol` is injected and the workspace env is
  set: rate is `total / window_minutes`, error_rate is
  `failed / total`, p95 passes through.
- `/api` returns 403 for a client bearer.
- `/ingestion` reports overdue polls and recent incidents seeded
  through the testcontainers Postgres.
- `/ingestion` returns 403 for a client bearer.
- `/db` returns `connection_count > 0` plus the unavailable shape
  for `slow_queries` (pg_stat_statements is not installed in the
  testcontainers `postgres:18-alpine` image — exactly the documented
  fallback path).
- `/db` returns 403 for a client bearer.

## Local-dev posture

Hitting the API locally against the testcontainers Postgres without
Azure credentials wired returns 200 from every endpoint:

- `/api` reports `data_source: "unavailable"` /
  `reason: "workspace id not configured"` per window.
- `/ingestion` returns real backlog + incident data from the
  local DB.
- `/db` returns a real connection count and the
  `extension not installed` envelope for slow queries.

This is the deliberate "degraded but usable" posture from
improvement-plan §12 — the admin UI can render per-source
unavailable badges instead of failing the whole page.

## Verification gate

```bash
uv run ruff check .              # All checks passed
uv run ruff format .             # 5 files reformatted (this branch's
                                 # new files only); rest unchanged
uv run pyright                   # 0 errors, 26 warnings (pre-existing
                                 # testcontainers stub-not-found)
uv run pytest                    # 537 passed, 4 skipped (fixture too
                                 # small), 1 deselected (nightly)
uv run pre-commit run --all-files
                                 # All hooks Passed (including the
                                 # regen-endpoints-md gate after one
                                 # auto-regenerate cycle for the new
                                 # admin routes).
```

Webapp gate skipped — WU7.2 doesn't touch the SPA. Re-runs in CI
on the next webapp-affecting branch.

## What's next

WU7.4 (next entry) lands on top of this branch — the admin audit
log surface uses the same `admin/` package and the same WU4.5
dep stack.
