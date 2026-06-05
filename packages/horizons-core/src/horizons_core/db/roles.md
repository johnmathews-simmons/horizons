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

## How `SET LOCAL app.user_id` will work (WU1.5)

RLS policies will key on `current_setting('app.user_id', true)`. The
repository layer will wrap every API request in a transaction and call
`SET LOCAL app.user_id = '<requesting client id>'` as the first
statement. `SET LOCAL` scopes the GUC to the current transaction so
connection-pool reuse cannot leak it between requests.

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
| `documents` | SELECT | SELECT, INSERT | — |
| `document_versions` | SELECT | SELECT, INSERT | — |
| `clauses` | SELECT | SELECT, INSERT | — |

`admin_bypass` deliberately has no static grants. Code paths that need
admin reach assume the role per-operation (`SET LOCAL ROLE
admin_bypass`) and rely on its `BYPASSRLS` to read across tenants
through the same `api_app`-granted tables. This keeps the grant surface
small and the elevation auditable.

The corpus grants follow the same shape across all three tables:
`api_app` reads (the public API exposes corpus rows to clients —
subscription-scope filtering is the API's job today, RLS will be the
second layer in WU1.4); `ingestion_worker` reads and writes (the
worker inserts new rows and reads its own prior writes during the
alignment pass that assigns `clause_uid`). Neither role gets UPDATE —
the append-only triggers would reject it anyway, but absent grants is
the cheaper first layer.

These grants are the loosest workable surface for the **WU1.x** API
and ingestion layers. RLS policies and read-scope narrowing for the
corpus tables land in **WU1.4**; see [schema.md](schema.md)
"Multi-tenant access (current state)" for the boundary.

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
