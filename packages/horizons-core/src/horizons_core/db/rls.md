# Row-Level Security architecture

This is the spec the RLS spine executes against. As of WU1.5 the
spine and its bracket are live: the `app_private` schema and
`current_scope()` SECURITY DEFINER function from WU1.3, the
`watchlists` private-state table created in WU1.4, policies on
`watchlists` plus the three corpus tables, and the
`horizons_core.db.session.get_session()` bracket from WU1.5 that binds
`app.user_id` per request. All protected tables also carry `FORCE ROW
LEVEL SECURITY` so the schema owner is subject to policies too —
`admin_bypass` (BYPASSRLS) is the only way out.

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

    SELECT set_config('app.user_id', '<requesting client id>', true);

`set_config(name, value, is_local => true)` is the parameter-binding-safe
equivalent of `SET LOCAL app.user_id = '...'` — `SET LOCAL` parses above
the parameter binder and rejects `$1` placeholders, so the function form
is what `session.py` actually issues.

`is_local => true` scopes the GUC to the current transaction, so
connection pool reuse cannot leak it between requests. `session.py` is
the only sanctioned issuer; imperative raw SQL via `sqlalchemy.text()`
is permitted **only** inside that module (enforced by
`tests/test_raw_sql_isolation.py`).

`current_scope()` reads this GUC, looks up the calling user's active
subscriptions, and returns the `(jurisdiction, sector)` set they are
entitled to read. If the GUC is unset, the function **raises** —
forgetting the bracket is a bug and a silently empty result set is
worse than a loud failure.

## Session contract (WU1.5)

The application reaches Postgres through one entry point:
`horizons_core.db.session.get_session()`. The bracket carries the
responsibilities the RLS spine assumes are already in place by the time
a query runs.

**Shape.** `get_session(user_id: uuid.UUID)` is an `@asynccontextmanager`
yielding an `AsyncSession`. It is FastAPI-Depends-shaped:

```python
async with get_session(user_id) as session:
    rows = await session.execute(select(Watchlist))
    ...
```

**What it does on entry:**

1. Acquires a connection from the engine's pool.
2. Begins a transaction.
3. Issues `SELECT set_config('app.user_id', :u, true)` so the policy's
   `current_setting('app.user_id')::uuid` cast succeeds.
4. Yields the session.

**What it does on exit:**

- Normal exit (no exception): commits the transaction.
- Exception exit: rolls back the transaction, then re-raises.

Either way the connection is returned to the pool. `SET LOCAL` GUCs
auto-clear at transaction end so per-request bleed is already
impossible; `DISCARD ALL` on checkin (below) is the defence-in-depth
second layer.

**Why `app.user_role` and `app.subscription_id` are not set.** The
improvement plan's WU1.5 spec lists three GUCs, but no policy and no
helper reads `app.user_role` or `app.subscription_id` today.
`current_scope()` derives the subscription set from
`app_private.user_subscriptions` keyed on `app.user_id` — there is no
single canonical `subscription_id` for a multi-subscription client.
Setting GUCs no consumer reads is cargo-cult, so the bracket sets
`app.user_id` only. When a real consumer for either of the other two
arrives (e.g. an admin-context predicate or a single-subscription
filter), the bracket grows the corresponding `set_config` call and the
journal entry for that work unit documents why.

**`DISCARD ALL` on pool checkin.** The engine carries a SQLAlchemy
`checkin` event handler that issues `DISCARD ALL` against the returning
connection. This clears every session-level GUC, prepared-statement
plan cache, advisory lock, cursor, and temp table — defence-in-depth
against any code path that issues `SET` (not `SET LOCAL`) and forgets
to clear it. At demo-scale row counts the plan-cache churn is
negligible.

Two implementation notes are load-bearing here, both forced by the
SQLAlchemy 2.x asyncpg adapter:

1. The checkin handler issues `DISCARD ALL` against
   `dbapi_connection.driver_connection` (the raw `asyncpg.Connection`)
   via `sqlalchemy.util.await_only`, **not** via a SQLAlchemy cursor.
   The adapter cursor wraps every execute in an implicit transaction,
   and Postgres rejects `DISCARD ALL` with "cannot run inside a
   transaction block" if invoked that way.
2. The engine is built with `connect_args={"statement_cache_size": 0}`.
   `DISCARD ALL` deallocates server-side prepared statements; asyncpg's
   client-side cache does not know they are gone, and the next execute
   on the same connection fails with `InvalidSQLStatementNameError`.
   Disabling the client cache trades a re-prepare per call for not
   carrying that stale-cache footgun.

**Raw-SQL isolation.** `sqlalchemy.text()` is the only sanctioned
imperative-SQL entry point and is permitted **only inside
`session.py`**. Models may use `text("uuidv7()")` and `text("now()")`
as declarative `server_default=` arguments — that is a SQL expression
literal for schema generation, not raw-SQL execution — and the
architectural test allow-lists `db/models/*.py` for this reason.
Everything else goes through the SQLAlchemy ORM via the session
`get_session()` yields. The architectural test
`tests/test_raw_sql_isolation.py` AST-walks every `.py` under
`packages/horizons-*/src/` and fails on any `text(...)` call outside the
allowlist.

**Admin code paths.** Admin readers that need to bypass RLS still
acquire a session through `get_session()` for the GUC + bracket
discipline, then `SET LOCAL ROLE admin_bypass` inside the txn. The
session module does not provide a separate "admin session" entry
point — the role switch is the carve-out, not the bracket.

## Planned policies

### Private state (WU1.4)

The private-state tables — `watchlists` (created in WU1.4),
`saved_queries`, `alerts`, future per-client surfaces — each carry a
`user_id` column and four policies that key off the session GUC. The
shape for `watchlists`, as shipped in WU1.4:

    CREATE POLICY watchlists_owner_select ON watchlists
        FOR SELECT TO api_app
        USING (user_id = current_setting('app.user_id')::uuid);

    CREATE POLICY watchlists_owner_insert ON watchlists
        FOR INSERT TO api_app
        WITH CHECK (user_id = current_setting('app.user_id')::uuid);

    CREATE POLICY watchlists_owner_update ON watchlists
        FOR UPDATE TO api_app
        USING      (user_id = current_setting('app.user_id')::uuid)
        WITH CHECK (user_id = current_setting('app.user_id')::uuid);

    CREATE POLICY watchlists_owner_delete ON watchlists
        FOR DELETE TO api_app
        USING (user_id = current_setting('app.user_id')::uuid);

The pattern: read-side `USING`, write-side `WITH CHECK`, both keyed
directly off the GUC. `UPDATE` carries both so a row cannot be quietly
re-keyed to another user. `current_scope()` is not used here — private
state isolation is a single-column predicate, not a corpus-scope join.

Future private-state tables (`saved_queries`, `alerts`, ...) follow the
same four-policy shape.

### Corpus scope (WU1.4)

The corpus tables (`documents`, `document_versions`, `clauses`, future
`change_events`) carry `jurisdiction` / `sector` columns (directly on
`documents`; reachable via FK from `document_versions` and `clauses`).
The `api_app` `USING` policy joins through `current_scope()`:

    CREATE POLICY documents_in_scope ON documents
        FOR SELECT TO api_app
        USING (
            EXISTS (
                SELECT 1 FROM app_private.current_scope() cs
                WHERE cs.jurisdiction = documents.jurisdiction
                  AND cs.sector       = documents.sector
            )
        );

`document_versions_in_scope` and `clauses_in_scope` reach scope by
joining through to the parent `documents` row — RLS predicates do not
transitively apply across tables, so each child table carries its own
`EXISTS` walking up to `documents` and then into `current_scope()`. The
schema is kept clean (no `jurisdiction` / `sector` duplicated onto the
child tables); the planner pushes the `EXISTS` to a hash join at
demo-scale row counts.

`ingestion_worker` writes corpus rows under its own role and needs an
**explicit pass-through policy** to bypass scope filtering — the worker
does not know which client will eventually read its writes, and once
RLS is enabled a role with no applicable policy is denied by default:

    CREATE POLICY documents_ingestion_all ON documents
        FOR ALL TO ingestion_worker
        USING (true) WITH CHECK (true);

`document_versions_ingestion_all` and `clauses_ingestion_all` are the
analogous policies on the child tables. The `FOR ALL` clause covers
SELECT/INSERT/UPDATE/DELETE; the append-only triggers from WU1.2 still
reject `UPDATE`, so the effective permission for the worker is
`SELECT + INSERT` (which matches the role-level grants).

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
| 3. Repository / app layer | The [repository layer](../repos/repos.md) reads through ORM `select()` only; writes name their owner via mandatory keyword `*, user_id`. Raw SQL outside `session.py` is AST-banned (`tests/test_raw_sql_isolation.py`). | The corpus repos return Pydantic DTOs from ORM queries on tables protected by `*_in_scope`; the role-bound session is what makes scope effective. |

Test discipline: multi-user integration tests run two concurrent
sessions with different `app.user_id` values and assert non-leakage at
the database boundary. Single-tenant unit tests are not enough.

## Status by table (end of WU1.4)

| Table | RLS enabled? | FORCE? | Policies |
| --- | --- | --- | --- |
| `users`, `subscriptions`, `subscription_scopes` | no | no | Reachable only via `current_scope()`. Direct-read RLS deferred until an API surface needs it (WU2.x). |
| `watchlists` | **yes** | **yes** | `watchlists_owner_select` / `_insert` / `_update` / `_delete` — all `TO api_app`, keyed on `app.user_id`. |
| `documents` | **yes** | **yes** | `documents_in_scope` (`TO api_app`, joins `current_scope()`); `documents_ingestion_all` (`TO ingestion_worker`, pass-through). |
| `document_versions` | **yes** | **yes** | `document_versions_in_scope` (`TO api_app`, joins through `documents`); `document_versions_ingestion_all` (`TO ingestion_worker`, pass-through). |
| `clauses` | **yes** | **yes** | `clauses_in_scope` (`TO api_app`, joins through `document_versions` → `documents`); `clauses_ingestion_all` (`TO ingestion_worker`, pass-through). |
| `app_private.current_scope()` | n/a | n/a | EXECUTE granted to `api_app` only. |

`admin_bypass` is not listed: BYPASSRLS is a role attribute, not a
policy, so it does not appear in any policy's `TO` clause. Sessions
that `SET LOCAL ROLE admin_bypass` see every row on every table —
audited per-operation, never granted statically.

## Status by gate (end of WU1.7)

The repository layer ([repos.md](../repos/repos.md)) is the third
defence-in-depth layer; the WU1.7 two-client integration gate
(`tests/isolation/test_private_state_isolation.py` and
`tests/isolation/test_corpus_subscription_isolation.py`) asserts that
both axes hold end-to-end through `get_session()` →
`SET LOCAL ROLE api_app` → `WatchlistsRepository` /
`DocumentsRepository` / `DocumentVersionsRepository` /
`ClausesRepository`. The plan flags this as the gate for Tracks 2 / 3 /
4 — no work in those tracks merges while either file is red.

## Related

- [roles.md](roles.md) — the role model, grants table, `app_private`
  function-EXECUTE grants.
- [schema.md](schema.md) — table definitions and `app_private` section.
- [repos/repos.md](../repos/repos.md) — the repository layer shape,
  `user_id` discipline, ORM-only rule.
- [design doc 3 §Multi-tenant isolation](../../../../../docs/3.%20database-design.md)
  — the principle.
- [design doc 4 §Defence-in-depth for isolation](../../../../../docs/4.%20services.md)
  — the layered enforcement story.
