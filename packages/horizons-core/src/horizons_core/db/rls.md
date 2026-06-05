# Row-Level Security architecture

This is the spec the next several work units execute against. Today (end
of WU1.3) the **mechanism** is in place — the `app_private` schema, the
`current_scope()` SECURITY DEFINER function — but no table yet has
`ENABLE ROW LEVEL SECURITY` or a policy attached. WU1.4 wires those.

Read this alongside [roles.md](roles.md) (the four-role grant model) and
[schema.md](schema.md) (the tables RLS will protect).

## Two axes of isolation

From [design doc 4 §Multi-tenant isolation](../../../../../docs/4.%20services.md):

1. **Cross-client privacy.** Client A cannot observe any state belonging
   to client B — watchlists, alerts, saved queries, dashboards,
   subscriptions. Predicate-keyed RLS on `app.user_id`.
2. **Subscription scope on the corpus.** A UK-only client cannot read
   corpus rows (documents, versions, clauses, change events) outside
   their `(jurisdiction, sector)` set. Predicate joins through
   `app_private.current_scope()`.

Both axes are treated with the same severity. Both must hold even when
the application is buggy.

## Two-schema posture

| Schema | Owner | What lives there | Who sees it |
| --- | --- | --- | --- |
| `public` | `schema_owner` | All application tables. | `api_app`, `ingestion_worker` (per-table grants). |
| `app_private` | `schema_owner` | SECURITY DEFINER helpers (`current_scope()`, future scope helpers). | `api_app` EXECUTE-only on individual functions. |

`api_app` has **no** `USAGE` on the `app_private` schema beyond what is
needed to invoke explicitly-granted functions. Direct table access from
inside `app_private` is impossible because the schema contains no
tables.

The SECURITY DEFINER + empty `search_path` contract is what lets the
function read the tenancy tables (owned by `schema_owner`) even when
called by `api_app`. Without the schema isolation, defining `EXECUTE`
narrowly would be tedious; with it, `api_app` having `USAGE` on
`app_private` is a clean, auditable surface.

## Session GUC: `app.user_id`

Every request the API handles is wrapped in a transaction whose first
statement is:

    SET LOCAL app.user_id = '<requesting client id>';

`SET LOCAL` scopes the GUC to the current transaction, so connection
pool reuse cannot leak it between requests. The repository layer is
responsible for issuing the `SET LOCAL`; raw SQL that bypasses the
repository is lint-banned.

`current_scope()` reads this GUC, looks up the calling user's active
subscriptions, and returns the `(jurisdiction, sector)` set they are
entitled to read. If the GUC is unset, the function **raises** —
forgetting `SET LOCAL` is a bug and a silently empty result set is
worse than a loud failure.

## Planned policies

### Private state (WU1.4)

The private-state tables — `watchlists` (lands in WU1.4),
`saved_queries`, `alerts`, future per-client surfaces — each carry a
`user_id` column and a `USING` policy of the shape:

    CREATE POLICY watchlists_owner_read ON watchlists
        FOR SELECT TO api_app
        USING (user_id = current_setting('app.user_id')::uuid);

    CREATE POLICY watchlists_owner_write ON watchlists
        FOR INSERT TO api_app
        WITH CHECK (user_id = current_setting('app.user_id')::uuid);

The pattern: read-side `USING`, write-side `WITH CHECK`, both keyed
directly off the GUC. `current_scope()` is not used here — private
state isolation is a single-column predicate, not a corpus-scope join.

### Corpus scope (WU1.4 or WU1.5)

The corpus tables (`documents`, `document_versions`, `clauses`, future
`change_events`) carry `jurisdiction` / `sector` columns (directly on
`documents`; reachable via FK from `document_versions` and `clauses`).
The `USING` policy joins through `current_scope()`:

    CREATE POLICY documents_in_scope ON documents
        FOR SELECT TO api_app
        USING (
            EXISTS (
                SELECT 1 FROM app_private.current_scope() cs
                WHERE cs.jurisdiction = documents.jurisdiction
                  AND cs.sector       = documents.sector
            )
        );

`ingestion_worker` writes corpus rows under its own role and is
**exempt** from these policies via the `TO api_app` clause — the worker
does not know which client will eventually read its writes.

### `admin_bypass`

`admin_bypass` carries `BYPASSRLS` (see [roles.md](roles.md)). Admin
code paths that need to read across tenants assume the role
per-operation:

    SET LOCAL ROLE admin_bypass;

This is the audited escape hatch. There is no policy carve-out for
admin; the role attribute is the carve-out.

## Defence-in-depth layers

For each axis, three independent layers must each prevent a leak:

| Layer | Cross-client | Corpus scope |
| --- | --- | --- |
| 1. Postgres grants | `api_app` granted on private-state tables; one-DB-user-per-tenant is not used. Grant alone is **insufficient**. | `api_app` SELECT on corpus tables. Grant alone is **insufficient**. |
| 2. RLS policy | `user_id = current_setting('app.user_id')::uuid`. | `EXISTS(... app_private.current_scope() ...)`. |
| 3. Repository / app layer | Repository sets `SET LOCAL app.user_id` per request; lint bans raw SQL outside repo. | Repository filters by subscription scope at query construction time (visibility on top of RLS). |

Test discipline: multi-user integration tests run two concurrent
sessions with different `app.user_id` values and assert non-leakage at
the database boundary. Single-tenant unit tests are not enough.

## Status by table (end of WU1.3)

| Table | RLS enabled? | Policy? | Notes |
| --- | --- | --- | --- |
| `users`, `subscriptions`, `subscription_scopes` | no | — | Reachable only via `current_scope()` today. RLS posture for these tables itself is a WU1.4 decision (likely `admin_bypass`-only or strict owner-read). |
| `documents`, `document_versions`, `clauses` | no | — | WU1.4 enables RLS + corpus-scope policy. |
| `watchlists` (not yet created) | n/a | — | Created in WU1.4 alongside its owner-read policy. |
| `app_private.current_scope()` | n/a | n/a | **Live** as of WU1.3. EXECUTE granted to `api_app` only. |

## Related

- [roles.md](roles.md) — the role model, grants table, `app_private`
  function-EXECUTE grants.
- [schema.md](schema.md) — table definitions and `app_private` section.
- [design doc 3 §Multi-tenant isolation](../../../../../docs/3.%20database-design.md)
  — the principle.
- [design doc 4 §Defence-in-depth for isolation](../../../../../docs/4.%20services.md)
  — the layered enforcement story.
