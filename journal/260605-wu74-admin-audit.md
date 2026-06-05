# 2026-06-05 — WU7.4 admin audit log surface

Exposes `GET /v1/admin/audit` — a filtered, paginated read view over
the `admin_access_log` table that the WU1.9 / WU4.5 admin context
managers write to. Read-only by construction; the table's
append-only invariant is pinned by a new architectural test.

## What shipped

### `core/repos/audit.py`

`AdminAccessLogReadRepository(session).search(...)` — a single
filter-and-list method that AND's the optional filters together and
returns the matching `AdminAccessLogDTO`s newest-first, capped at
`limit`. Filters: `since`, `admin_id`, `target_user_id`, `action`
(operator | impersonation), `limit`. The repo trusts whatever the
caller passes — defaulting `since` to "now - 24h" and clamping
`limit` to 500 lives at the route layer.

The DTO is the same `AdminAccessLogDTO` the writer repo
(`repos/admin_access_log.AdminAccessLogRepository`) returns, so
admin tooling can read what audited paths wrote without an extra
mapping layer.

### `admin/audit.py`

One GET route under `/v1/admin/audit`. All five filters are
optional query params; `since` defaults to `datetime.now(UTC) -
timedelta(hours=24)` when omitted; `limit` defaults to 100 and is
silently clamped at 500. Response envelope:

```json
{
  "since": "<ISO 8601>",
  "limit": <effective_limit>,
  "count": <returned row count>,
  "rows": [ { id, admin_id, target_user_id, mode, token_id, reason, granted_at } ]
}
```

Echoing the effective `since` + `limit` lets the SPA render
"showing X rows since YYYY-MM-DD" without re-computing the defaults
itself.

The route reuses the WU4.5 admin dep stack
(`require_admin_principal` + `admin_operator_session_for_request`).
That means every audit-log read itself writes one
`admin_access_log` row before the body runs — which is the right
posture: an admin querying the audit trail is itself an audited
event, and the new row is visible to subsequent queries.

### `tests/api/test_admin_audit_endpoint.py`

7 integration tests:

1. **Round-trip** — admin POSTs a subscription, POSTs another, then
   PATCHes the first; `/v1/admin/audit?admin_id=<self>` returns at
   least 4 rows (3 from the seeded writes + 1 from this GET) all
   with `mode='operator'` and `target_user_id=null`.
2. **Filter by action** — `action=impersonation` returns 0 rows
   (none seeded; the read's own audit row is filtered out by
   `mode != 'impersonation'`).
3. **Filter by since (future)** — `since` set to tomorrow returns
   0 rows.
4. **Non-admin → 403** — client bearer hits `/v1/admin/audit`,
   gets 403 `"admin role required"` (per the documented
   `/v1/admin/*` exception to the 404-not-403 rule).
5. **Invalid uuid → 422** — `admin_id=not-a-uuid` rejected by
   FastAPI's query validation.
6. **Invalid action → 422** — `action=definitely-not-a-mode`
   rejected by the `AdminAccessMode` enum validation.
7. **Limit clamp** — `limit=100_000` returns 200 with `limit=500`
   in the response envelope (silent clamp).

### `tests/api/test_admin_access_log_append_only.py`

Architectural test, two assertions:

- No application role (excluding `schema_owner`, which owns DDL)
  holds DELETE or UPDATE on `admin_access_log`. Pulled from
  `information_schema.role_table_grants`.
- No RLS policy on `admin_access_log` permits `DELETE`, `UPDATE`,
  or `ALL`. Pulled from `pg_policies`.

WU1.0 / WU1.9 set this up (`RLS ENABLE`+`FORCE`, no policy,
`admin_bypass` granted only `SELECT, INSERT`, BEFORE
UPDATE/DELETE triggers raise). WU7.4 introduces a read surface;
this test is the regression net that catches a future migration
silently granting a write privilege or attaching a deletion
policy. The pair takes 0.4 s — cheap insurance.

## Why this lives next to the write side

The writer repo
(`packages/horizons-core/src/horizons_core/repos/admin_access_log.py`)
stays untouched. The new reader is a sibling module
(`audit.py`) rather than a method on the writer because the two
have different shapes and different access discipline:

- The writer is owned by the `core.auth.admin` context managers;
  no other call site should reach it.
- The reader is a query surface for admin tooling. The repo it
  exposes is a thin wrapper over a single filter-AND-list shape.

Co-locating the two would invite a future caller to reach for the
writer from the read path, eroding the audited-elevation invariant.
Two modules, one shared DTO, single direction of read/write
responsibility.

## Verification gate

```bash
uv run ruff check .              # All checks passed
uv run pyright                   # 0 errors
uv run pytest                    # 537 passed, 4 skipped, 1 deselected
uv run pre-commit run --all-files
                                 # All hooks Passed (including the
                                 # auto-regenerated endpoints.md after
                                 # the /v1/admin/audit and three
                                 # /v1/admin/health routes landed)
```

The full suite (WU7.2 + WU7.4 together) added 16 tests — 7 for the
audit endpoint, 2 for the append-only architectural pin, 7 for the
three health endpoints. All previously-green suites stay green.

## What's next

Tracks 7 / 8 still have outstanding work:

| WU | Title | Notes |
| --- | --- | --- |
| 8.3 | Demo runbook | Pre-demo checklist + demo script + recovery steps. |
| 8.4 | Pre-demo wrap | Journal + CLAUDE.md commands section update. |

WU7.2 + WU7.4 close the Track 7 admin-side surface — the
`/v1/admin/health/*` and `/v1/admin/audit` endpoints together give
the SPA enough to render a real "operator dashboard" view backed
by Log Analytics + the demo's Postgres.
