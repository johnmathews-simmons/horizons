# 2026-06-05 — WU4.5: admin subscription endpoints

*Last revised: 2026-06-05.*
*Path: journal/260605-wu45-admin-subscriptions.md.*

Closes Track 4's admin write surface. Adds `/v1/admin/subscriptions`
GET / POST / PATCH on top of the WU1.9 admin-bypass context manager
plus a small migration (0011) that gives the subscription ledger a
soft-delete mode and the watchlists table a soft-hide flag.

## What shipped

### Migration `0011_admin_subscription_endpoints_support.py`

- `subscription_scopes.valid_to timestamptz NULL` — the soft-delete
  column. The append-only trigger from WU1.1 relaxes from
  "reject every UPDATE" to "allow `valid_to` NULL → timestamp once
  with every other column unchanged", mirroring the existing
  `subscriptions` trigger.
- `app_private.current_scope()` updated to filter scope rows by
  `valid_to` (NULL or in the future). A soft-deleted scope row stops
  contributing to a client's read surface immediately. The
  subscription-level `valid_from` / `valid_to` filter from WU1.3 stays
  in place; the two windows compose.
- `watchlists.active boolean NOT NULL DEFAULT true` — the soft-hide
  flag. The client read path
  (`WatchlistsRepository.list_for`) filters `active = true`; admin
  sees both via `list_all_including_inactive_for_user`.
- `admin_bypass` grants extended for the new endpoint surface:
  `SELECT` on `users`, `SELECT, INSERT` on `subscriptions`,
  `SELECT, INSERT, UPDATE` on `subscription_scopes` (UPDATE
  trigger-policed), and `UPDATE` on `watchlists` (soft-hide path).
  Notably **no `UPDATE` on `subscriptions`** — PATCH only mutates
  scope rows; ending a subscription would happen via a separate
  endpoint that also writes a replacement.

### Repository layer

- **`SubscriptionsRepository`** (new file
  `repos/subscriptions.py`) — `list_for_user`, `get_by_id`,
  `create_for_user`, `add_scopes`, `soft_delete_scopes`,
  `active_scope_documents`. The last one joins
  `subscription_scopes` → `documents` under the admin's bound session
  to compute the in-scope document set the reduction path needs for
  the watchlist soft-hide pass. We reach the join through the
  expression layer rather than `app_private.current_scope()` — admin
  sessions are bound to the admin's id, not the target's.
- **`WatchlistsRepository`** gains two methods:
  - `list_for` now filters `active = true` (was unfiltered).
  - `list_all_including_inactive_for_user(user_id)` for admin views.
  - `soft_hide_out_of_scope(user_id, in_scope_document_ids)` is the
    bulk UPDATE the reduction path uses. Idempotent — already-inactive
    rows aren't re-touched.

### Route layer

- **`packages/horizons-api/src/horizons_api/routes/admin_subscriptions.py`** —
  three endpoints under `/v1/admin/subscriptions`:
  - `GET ?user_id=<uuid>` lists target's subscriptions + scope history
    (active + soft-deleted scope rows).
  - `POST` creates a subscription with a non-empty scope set; the
    target user must exist (404 otherwise).
  - `PATCH /{id}` does add and/or remove. Add = `INSERT` new scope
    rows; remove = soft-delete (`valid_to` set on existing rows).
    Rejects no-op patches (422), overlap between add and remove (422),
    adds that are already active (422), removes that are not active
    (422). After the scope mutation, the route computes the user's
    post-reduction in-scope document set and soft-hides every active
    watchlist for the user whose document falls outside it. The
    response includes counts: `scopes_added`, `scopes_removed`,
    `watchlists_soft_hidden`.
- **`deps/admin.py`** — `require_admin_principal` (401 → 403 for
  non-admin) and `admin_operator_session_for_request` (wraps
  `admin_operator_session` from WU1.9, yielding an `admin_bypass`
  session and writing one `admin_access_log` row per request before
  the route body runs).
- App-shell mounts the new router in `app.py`. No other route
  touched.

### Tests

`tests/test_admin_subscription_endpoints.py` — the four scenarios the
WU4.5 acceptance lists:

1. **`test_admin_post_subscription_shows_up_in_client_me`** — admin
   POSTs a subscription with two scope pairs; the client logs in
   immediately after and `/v1/me` returns both pairs in
   `subscription.scope`.
2. **`test_admin_patch_reduction_soft_hides_out_of_scope_watchlist`** —
   client has two watchlists, one per scope. Admin PATCHes the
   subscription removing one scope. The response reports
   `scopes_removed == 1`, `watchlists_soft_hidden == 1`, and the
   dropped scope row has `valid_to != null`. Client's
   `GET /v1/me/watchlists` returns only the keep-doc; an admin-bypass
   SELECT against `watchlists` shows `active = false` on the dropped
   row (not deleted).
3. **`test_non_admin_calling_admin_endpoint_returns_403`** — a client
   bearer probes GET / POST / PATCH; each returns 403 with the
   `"admin role required"` body. Documented exception to the "404 not
   403" rule for private-state endpoints: `/v1/admin/*` is
   administrative, so 403 is the right signal for the SPA's admin
   route guard.
4. **`test_admin_write_creates_one_audit_row_per_request`** — admin
   POST + PATCH each create exactly one `admin_access_log` row.
   Both rows have `mode = 'operator'` and `target_user_id IS NULL`,
   matching the WU1.9 contract.

`tests/test_watchlists_migration.py::test_watchlists_grants` updated:
`admin_bypass` now holds `{SELECT, UPDATE}` (was `{SELECT}` only) —
inline comment explains the WU4.5 reduction path.

### Doc updates

- `db/roles.md` — per-table grants table now shows admin_bypass'
  SELECT on `users`, SELECT/INSERT on `subscriptions`,
  SELECT/INSERT/UPDATE on `subscription_scopes`, SELECT/UPDATE on
  `watchlists`. The closing paragraph reframes admin_bypass from
  "mostly SELECT" to "mostly read-only with narrow, purpose-built
  writes" (audit log; tenancy ledger writes; soft-hide).
- `db/schema.md` — `subscription_scopes` row adds the `valid_to`
  column and the trigger's allowed transition. `watchlists` row adds
  `active` and `document_id`. Append-only enforcement section
  updates the `subscription_scopes` trigger description.

## Design decisions worth keeping

1. **PATCH is append-only at the row level.** Adds insert new
   `subscription_scopes` rows; removes flip `valid_to` on existing
   rows. We deliberately do NOT mutate any existing scope row's
   `(jurisdiction, sector)`. The subscription's `id` stays stable
   across PATCH calls — the SPA can hold a stable subscription handle
   while editing scope membership.
2. **`current_scope()` filters both subscription and scope `valid_to`.**
   The two windows compose: a subscription's `valid_to` ends every
   scope under it; a scope's `valid_to` ends just that pair. Tests
   exercise both gates indirectly through the reduction path.
3. **Watchlist soft-hide, not delete.** The user may re-add the scope
   later; the row's `active` flag is reversible, deletion is not. The
   acceptance test inspects the DB to confirm the row still exists.
4. **No `UPDATE` on `subscriptions` for `admin_bypass`.** PATCH only
   mutates scope rows; ending a subscription would be a separate
   endpoint that also writes a replacement.
5. **403 not 404 on `/v1/admin/*`.** A client bearer presenting a
   valid access token to an admin URL gets `403 "admin role required"`
   so the SPA can render an explicit error. Concealing the prefix
   with 404 buys nothing for an authenticated caller who can read
   OpenAPI.
6. **`require_admin_principal` depends on `authenticated_user`** —
   a missing or invalid bearer still produces the uniform 401 at the
   bearer dep, and only an authenticated-but-not-admin caller gets
   403. The status-code distinction is meaningful at exactly that
   boundary.
7. **Audit row commit semantics are preserved from WU1.9.** The
   `admin_operator_session` context manager commits the audit row in
   its own short transaction *before* yielding the working session.
   A 422 raised by the route body (invalid PATCH shape) still leaves
   the audit row in place — the elevation happened the moment the row
   was issued, regardless of what the body went on to do.

## Status by suite (end of WU4.5)

- 515 passing (was 511 → +4 admin endpoint tests; no regressions in
  watchlist / scope / migration suites).
- `ruff check`, `ruff format`, pyright strict: clean.
- WU0.2's raw-SQL architectural test still passes (the new route
  file and repo file contain zero `sqlalchemy.text()` calls; the
  expression layer carries everything).

## What's next

WU4.6 (OpenAPI + endpoints.md regenerator) and the unblocked WU7.2
(admin health endpoints) and WU7.4 (admin audit log surface). The
audit-log surface is straightforward — it reads
`admin_access_log` rows owned by the calling admin or by any
admin within a window, both already grant-allowed.
