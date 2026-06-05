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
