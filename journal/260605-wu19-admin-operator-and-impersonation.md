# 2026-06-05 — WU1.9: admin operator + impersonation paths

Closes Track 1. With WU1.6 (repos) + WU1.7 (two-client gate) + WU1.8
(property test, nightly) already in place, this unit adds the only
sanctioned entry points for admin code paths and the append-only audit
trail those paths write to.

## What shipped

1. **Migration `0006_admin_access_log.py`** — new `admin_access_log`
   table with `(admin_id, target_user_id, mode, token_id, reason,
   granted_at)` shape. UUIDv7 PK. CHECK constraint enforces the
   mode/target invariant: `operator` requires `target_user_id IS NULL`;
   `impersonation` requires it to be set. Append-only via two triggers
   (BEFORE UPDATE / BEFORE DELETE both reject). RLS enabled and `FORCE`d
   for defence in depth, **no policy attached** — only `admin_bypass`
   (BYPASSRLS) reads / writes, so default-deny holds for every other
   role. Grants: `schema_owner` owns DDL; `admin_bypass` gets
   `SELECT, INSERT`; `api_app` and `ingestion_worker` get nothing.

2. **ORM model `db/models/admin_access_log.py`** — mirrors the migration
   exactly. `AdminAccessMode` StrEnum mirrors the Postgres ENUM type.

3. **Session helpers in `db/session.py`** — two narrow public helpers
   so the `text()` carve-out stays single-file:
   - `set_local_role(session, role)` validates against a frozen
     allow-list (`{"admin_bypass", "api_app"}`) before issuing
     `SET LOCAL ROLE <role>`. The role name is interpolated because
     `SET LOCAL` parses above the parameter binder; the allow-list is
     the safety net.
   - `bind_impersonation_admin_id(session, admin_id)` issues
     `SELECT set_config('app.impersonating_admin_id', :a, true)`.

   Also renamed `_get_engine()` → `get_engine()` (it's now legitimately
   cross-module).

4. **`repos/admin_access_log.py`** — `AdminAccessLogRepository` with
   `record(...)` and `list_for_admin(...)`. `AdminAccessLogDTO` is a
   frozen Pydantic v2 model.

5. **`core/auth/admin.py`** — two async context managers:
   - `admin_operator_session(admin_id, *, engine=None, reason=None)` —
     `admin_bypass` role, cross-tenant reads. Audit row's
     `target_user_id` is `None`.
   - `admin_impersonation_session(admin_id, target_user_id, *, engine,
     reason)` — `api_app` role under the target's `app.user_id`;
     admin's id captured in `app.impersonating_admin_id`. RLS fires
     exactly as for a real client request from the target.

   **Audit semantics.** Both write the audit row in a *separate*
   `session_for_user` bracket that commits before the working session
   is yielded. If the caller's body raises and rolls the working
   session back, the audit row still persists — the elevation
   happened the moment the row was issued.

6. **Integration test `tests/isolation/test_admin_paths.py`** — three
   scenarios assert through `WatchlistsRepository`:
   - Operator sees both clients' watchlists; exactly one
     `mode='operator'` audit row written.
   - Impersonation of A sees only A's watchlists; exactly one
     `mode='impersonation'` audit row written with
     `target_user_id = A`. The yielded session reports the admin's
     id under `app.impersonating_admin_id`.
   - After both admin contexts exit, a normal client session for B
     sees only B's rows and the impersonation GUC has been cleared
     (the `DISCARD ALL`-on-checkin from WU1.5 is the safety net).

7. **`tests/test_session_helpers.py`** — unit test for the
   `set_local_role` allow-list validation (covers the
   `ValueError` branch that the integration suite doesn't exercise).

8. **Doc updates** — `db/rls.md` §Admin code paths, §Audit log table,
   §Status by gate (now "end of WU1.9"), §Status by table (adds
   `admin_access_log`). `db/roles.md` per-table grant matrix and the
   "admin_bypass writes its own audit trail" paragraph.
   `db/schema.md` aggregate description + append-only enforcement
   row + multi-tenant access closing paragraph.

9. **`tests/isolation/conftest.py`** — `TwoClients` gains an
   `admin_id` field (a `users` row with `role='admin'`) so the
   `admin_access_log.admin_id` FK resolves. `_make_user` learns an
   optional `role` parameter (default `"client"`).

## Open questions resolved this session

Per the prompt's Q1–Q4 — all four took the recommended branch:

1. `app.impersonating_admin_id` binds only inside
   `admin_impersonation_session`; `session_for_user` stays narrow.
2. Raw SQL stays in `db/session.py` via two new helpers; `auth/admin.py`
   contains zero `sqlalchemy.text()` calls.
3. `AdminAccessLogRepository.record()` is the only write site — keeps
   the door open for Track 4's token-mint / refresh seams.
4. A placeholder `uuid.uuid4()` token id is minted per session and
   written into `token_id`; the column stays nullable so Track 4 can
   swap in real JWT ids without a schema-shape change. Tests assert
   `token_id IS NOT NULL`.

## Status by suite (end of WU1.9)

- 90 default-marker tests passing (was 86 → +3 admin paths + 1 unit).
- 100 % line + branch coverage on tracked Python source.
- ruff format + check, pyright strict, pre-commit all-files: clean.
- Webapp gate (lint:check / build / vitest): clean. Webapp untouched
  by this unit.
- Property test (`-m nightly`): not run this session; gate is
  non-gating by design and last passed on the previous run
  (27009724940).

## Track 1 status

WU1.9 is the last unit in Track 1. The two-axis isolation spine
(WU1.5 bracket → WU1.6 repos → WU1.7 two-client gate → WU1.8 property
test → WU1.9 admin entry points) is now closed for Track 1's purposes:
client sessions filter through RLS, the repository layer is the
defence-in-depth third layer, the gate tests + property test prove the
invariant, and admin sessions are the only audited carve-out.

Track 2 (alignment) and Track 3 (ingestion) are unblocked. Track 4
(FastAPI) is unblocked architecturally — the public API surface and
the `/v2/admin/*` HTTP exposure both build on the WU1.9 context
managers.

## Cadence note

Worktree-driven flow held the same shape as WU1.8: `EnterWorktree
eng-wu1.9-admin-impersonation` → mirror run pointer → resolve open
questions → doc + tests + code → local sweep (ruff / pyright / pytest
/ pre-commit / webapp) → `/done`. Direct push to main, ff-merge,
remote branch cleanup, `ExitWorktree(remove, discard_changes=true)`.
