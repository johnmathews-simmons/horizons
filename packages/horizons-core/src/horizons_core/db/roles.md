# Postgres role model

Four roles, all `NOLOGIN`. They are permission containers — application
services do not connect *as* them; per-environment LOGIN users are
provisioned by ops/IaC and granted the appropriate role.

| Role | BYPASSRLS | Owns DDL | Purpose |
| --- | --- | --- | --- |
| `schema_owner` | no | yes | Owns tables, indexes, sequences. Used by migrations only. |
| `api_app` | no | no | Public API service. Reads/writes within RLS. |
| `ingestion_worker` | no | no | Ingestion worker. Writes corpus rows; cannot read client-private state. |
| `admin_bypass` | **yes** | no | Audited admin escape hatch. Used only through explicit, logged code paths. |

## Why NOLOGIN

Keeping the four roles as permission containers separates two concerns
that have different rotation cadences:

- **Permission grants** are part of schema. They live in migrations and
  change rarely. They are the same in every environment.
- **Connection credentials** rotate per environment and per incident.
  They live in secret storage, not in source control.

A per-env LOGIN user (e.g. `app_user_prod`) is created out-of-band by
ops/IaC, granted the appropriate role (e.g. `GRANT api_app TO
app_user_prod`), and rotated independently. Reissuing a password never
touches a migration.

## Why two non-bypass app roles

`api_app` and `ingestion_worker` both have `NOBYPASSRLS`, but they
exist as distinct roles because their **read scope is different**:

- The API service answers client requests and must see client-private
  state — watchlists, alerts, saved queries — for the requesting client
  only. RLS policies on those tables key off `current_setting('app.user_id')`.
- The ingestion worker writes corpus rows (documents, versions, clauses)
  and is not authorised to read any client-private state. A separate role
  lets us GRANT on a per-table basis instead of relying solely on
  predicate-level isolation.

This is the defence-in-depth posture from `docs/4. services.md`:
RLS + role-grants + repository layer, not RLS alone.

## Why `admin_bypass` is separate from a generic `admin`

`BYPASSRLS` is irreversible at query time — once a session has it, the
session can read every row in every tenant. We want that capability
audited and rare, so it lives in its own role and is only ever assumed
through an explicit, logged code path (e.g. an admin support tool).
The default admin operator user does **not** have `BYPASSRLS`; it
escalates to `admin_bypass` per-operation via `SET LOCAL ROLE`.

## How `app.user_id` is bound per request (WU1.5)

RLS policies key on `current_setting('app.user_id')::uuid` (watchlists)
or `current_setting('app.user_id', true)` (via `current_scope()`).
`horizons_core.db.session.get_session()` is the sanctioned binder: it
opens a transaction and issues `SELECT set_config('app.user_id', :u,
true)` before yielding the session. `is_local => true` scopes the GUC
to the transaction so connection-pool reuse cannot leak it between
requests; `DISCARD ALL` on pool checkin is the defence-in-depth second
layer. See [rls.md](rls.md) §Session contract for the full bracket
shape and the SQLAlchemy/asyncpg implementation notes.

The session-GUC + RLS-predicate pair is one layer; the role-level GRANT
narrowing (above) is the second. Both must independently prevent a
cross-tenant read.

## Per-table grants (current state)

The role-model migration (`0001_role_model.py`) creates the four roles
but grants nothing on its own — there were no tables yet. Subsequent
migrations grant per-table:

| Table | `api_app` | `ingestion_worker` | `admin_bypass` |
| --- | --- | --- | --- |
| `users` | SELECT, INSERT, UPDATE | — | — |
| `subscriptions` | SELECT, INSERT, UPDATE *(trigger-policed)* | — | — |
| `subscription_scopes` | SELECT, INSERT | — | — |
| `documents` | SELECT *(RLS: in-scope)* | SELECT, INSERT *(RLS: pass-through)* | SELECT *(BYPASSRLS)* |
| `document_versions` | SELECT *(RLS: in-scope)* | SELECT, INSERT, UPDATE *(valid_to only — trigger-policed)* | SELECT *(BYPASSRLS)* |
| `clauses` | SELECT *(RLS: in-scope)* | SELECT, INSERT *(RLS: pass-through)* | SELECT *(BYPASSRLS)* |
| `watchlists` | SELECT, INSERT, UPDATE, DELETE *(RLS: owner-only)* | — | SELECT *(BYPASSRLS)* |
| `admin_access_log` | — | — | SELECT, INSERT *(RLS enabled, no policy)* |
| `document_poll_schedule` | — | SELECT, INSERT, UPDATE | — |
| `ingestion_incident` | — | SELECT, INSERT | — |

`admin_bypass` carries SELECT on the RLS-protected tables only —
**no INSERT, no UPDATE, no DELETE anywhere**, with one exception:
`admin_access_log` is where the role writes its own audit trail (one
INSERT per session). Append-only triggers on the table reject UPDATE
and DELETE outright, so the role's write surface is irreducibly
"append a new audit row, never modify history." Postgres' `BYPASSRLS`
attribute bypasses *row-level* security but does **not** override
table-level GRANTs, so the role is unusable without at least SELECT
on whatever it needs to read. Granting SELECT (and nothing else)
elsewhere gives the audited-elevation code path enough reach for
cross-tenant support work without becoming a back-door write surface.
The tenancy tables (`users`, `subscriptions`, `subscription_scopes`)
are deliberately omitted from `admin_bypass` until a real admin
reader exists — adding the grant is cheap and small-blast-radius if
and when WU2.x needs it.

The corpus grants follow the same shape across `documents` and `clauses`:
`api_app` reads (the public API exposes corpus rows to clients —
subscription-scope filtering is the API's job today, RLS will be the
second layer in WU1.4); `ingestion_worker` reads and writes (the
worker inserts new rows and reads its own prior writes during the
alignment pass that assigns `clause_uid`). Neither role gets UPDATE on
those two — the append-only triggers reject it anyway, but absent
grants is the cheaper first layer.

`document_versions` is the one exception. WU3.1 adds three
ingestion-side columns (`version_no`, `valid_from`, `valid_to`) and
narrows the append-only trigger so it permits `UPDATE` iff `valid_to`
is the only column that changed. `ingestion_worker` is granted
`UPDATE (valid_to)` (column-scoped — the trigger is the substantive
rule; the column grant is the cheap outer fence). This supports the
ingestion path documented in `docs/4. services.md` §"Ingestion
service": on an unchanged poll the worker extends the live version's
`valid_to`, and on a changed poll it closes the previous version's
`valid_to` before inserting the new row. No other role gets `UPDATE`
on `document_versions` — `api_app` is read-only, `admin_bypass` has
no static write.

The corpus grants are now the **outer** layer of a two-layer surface:
RLS policies (WU1.4) filter `api_app`'s SELECTs down to the
subscription scope, and the `ingestion_worker` pass-through policies
let the worker keep writing without RLS filtering. The role-level
grants are unchanged from WU1.2 — RLS narrows what the grants permit;
it does not add reach. See [rls.md](rls.md) for the policy shapes and
[schema.md](schema.md) "Multi-tenant access (current state)" for the
end-to-end boundary.

## Per-function grants (`app_private` schema)

The `app_private` schema (added in WU1.3) carries SECURITY DEFINER
helpers that RLS policies will invoke. The schema itself is owned by
`schema_owner`. `PUBLIC` is revoked from the schema; only the explicit
EXECUTE grants below let any role reach in.

| Function | `api_app` | `ingestion_worker` | `admin_bypass` |
| --- | --- | --- | --- |
| `app_private.current_scope() -> (jurisdiction, sector)` | EXECUTE | — | — |

`current_scope()` is the only function in `app_private` today. WU1.4's
corpus-scope RLS policies invoke it under `api_app`'s session; the
function runs with `schema_owner`'s privileges via SECURITY DEFINER so
it can read `subscriptions` / `subscription_scopes` even though
`api_app` itself does not have those rows under RLS. See
[rls.md](rls.md) for the full architecture.

## Running the migration

The role-model migration is `migrations/versions/0001_role_model.py`.
It is idempotent — re-running against a partially-set-up DB will not
fail. To apply:

```bash
export HORIZONS_DB_URL='postgresql+psycopg://user:pw@host:5432/db'
uv run alembic upgrade head
```

The integration test in `tests/test_role_model_migration.py` exercises
this end-to-end against a fresh Postgres 18 container and asserts each
role exists with the expected `rolbypassrls` / `rolcanlogin` /
`rolcreatedb` / `rolcreaterole` attributes.
