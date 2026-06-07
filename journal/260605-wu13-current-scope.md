# 2026-06-05 — WU1.3 `app_private.current_scope()`

*Last revised: 2026-06-05.*
*Path: journal/260605-wu13-current-scope.md.*

Third unit landed today. `app_private` schema + the SECURITY DEFINER
function the upcoming corpus RLS policies will invoke.

## What shipped

- Migration `0004_current_scope.py` — `app_private` schema (owned by
  `schema_owner`, PUBLIC revoked, USAGE granted to `api_app` only),
  and `app_private.current_scope() RETURNS TABLE(jurisdiction text,
  sector text) LANGUAGE plpgsql STABLE SECURITY DEFINER SET
  search_path = ''`. Reads `app.user_id` GUC, joins
  `public.subscriptions` + `public.subscription_scopes`, filters to
  currently active rows, returns the DISTINCT set. Owner reassigned to
  `schema_owner`; REVOKE PUBLIC; GRANT EXECUTE TO api_app.
- `tests/test_current_scope_migration.py` — 9 integration tests
  covering schema metadata, function signature (incl. `proconfig`),
  privilege matrix via `has_function_privilege` (api_app / ingestion /
  admin_bypass / PUBLIC), and six behavioural shapes (active / expired
  / future / overlapping / unset GUC raises / per-user isolation).
- `db/rls.md` — new doc, the architecture spec WU1.4 executes against:
  two isolation axes, two-schema posture, planned policies for private
  state vs corpus, defence-in-depth layers, per-table status table.
- `db/schema.md` — appended `app_private` section describing the
  function contract; pointer to `rls.md`.
- `db/roles.md` — added per-function grants section (the EXECUTE
  matrix for `app_private.*`).

Also a small precursor commit: `Apply ruff format to clear drift on
WU1.2 files` — fixed format drift in `0003_corpus_tables.py` and
`tests/test_corpus_tables_migration.py` that had been failing
`ruff-format` on `main` since efe9106. No behavioural change.

## Decisions

Three questions came in with the preamble; all three resolved with the
recommended option:

- **Missing GUC → RAISE.** When `app.user_id` is unset, the function
  raises rather than returning zero rows. RLS-protected queries that
  forgot to bracket the transaction with the GUC are bugs; a
  silently-empty result set hides them and looks like "no data" to the
  caller. Loud is better.
- **Overlap → DISTINCT.** Two active subscriptions both covering
  `(UK, BANKING)` collapse to one row. Scope is set-semantics;
  duplicates would be noise downstream.
- **`watchlists` deferred to WU1.4.** The plan's original WU1.2 had
  bundled `watchlists` with the corpus stub; we shipped the full
  corpus tables under WU1.2 instead and left the watchlists table
  unshipped. Folding it into WU1.4 means the table arrives alongside
  its owner-read RLS policy and they can be tested as a unit.

## Surprises / gotchas

1. **`SET LOCAL` does not accept parameters.** Postgres returns
   `syntax error at "$1"` because `SET LOCAL` is parsed at a layer
   above the parameter binder. Tests use the equivalent
   `SELECT set_config('app.user_id', :u, true)` (the `true` is the
   `is_local` arg → same `SET LOCAL` semantics, parameterised
   cleanly). The repository-layer code in WU1.5 will hit the same wall
   and should reach for `set_config` from the start rather than
   string-concatenating UUIDs into a SET LOCAL.
2. **`SET search_path = ''` doesn't remove `pg_catalog`.** Postgres
   still searches `pg_catalog` first when it's not explicitly in the
   path, so `now()` and `current_setting()` still resolve. The
   migration plays it safe anyway and uses `pg_catalog.now()` /
   `pg_catalog.current_setting()` — explicit qualification is part of
   the SECURITY DEFINER hygiene contract, not just a workaround.
3. **CI on `main` was already red.** efe9106 (WU1.2 journal commit)
   left format drift on `0003_corpus_tables.py` and
   `tests/test_corpus_tables_migration.py` that `ruff format --check`
   rejects. Pre-commit must have been bypassed on the offending
   commits. Cleared in the precursor commit so the WU1.3 commit lands
   green.
4. **Per-test data isolation in integration tests.** The
   `migrated_engine` fixture is function-scoped but the
   `postgres_container` is session-scoped. Data persists across tests
   within a session, so tests must use unique email values per case
   or they collide on `users.email UNIQUE`. Used file-local prefixes
   (`iso_a@`, `iso_b@`, `active@`, etc.) to avoid the existing
   tenancy-test slugs.
5. **Test the privilege matrix, not just the existence of the grant.**
   `has_function_privilege('public', 'app_private.current_scope()',
   'EXECUTE')` is the test that catches a forgotten `REVOKE EXECUTE …
   FROM PUBLIC`. Postgres grants EXECUTE to PUBLIC by default on every
   new function; the REVOKE in the migration is load-bearing.
6. **No alembic-check this WU.** WU1.3 adds no SQLAlchemy models —
   just a SQL function. Autogen drift checks compare ORM metadata to
   the live schema; with no model, there's nothing to drift against.
   The previous WU's drift check is still valid for the corpus
   tables.

## Plan-drift this session

Same shape as WU1.2: the plan's original WU1.3 acceptance ("`app_private`
schema + `current_scope()` function") matched what landed exactly. No
scope creep. The Q3 watchlists deferral is the only drift, and it's a
deliberate plan adjustment (was already inconsistent between the
plan's WU1.2 and what shipped as WU1.2).

## What's next

WU1.4 — the RLS spine. Per `db/rls.md`, that unit:

1. Creates `watchlists` (private state, owner-read pattern from
   `rls.md`).
2. Enables RLS on `watchlists` + the corpus tables.
3. Defines `watchlists_owner_read` / `watchlists_owner_write`
   policies (predicate on `app.user_id`).
4. Defines `documents_in_scope` (and analogues for
   `document_versions`, `clauses`) policies invoking
   `app_private.current_scope()`.
5. Multi-user integration tests asserting non-leakage at the database
   boundary.

The `rls.md` spec is the target. The function contract is fixed; the
next session is wiring policies.

## Outstanding (manual)

Branch protection on `main` for both CI lanes — still pending in the
GitHub UI from earlier sessions. The CI-on-main red status today is a
symptom: protected `main` would have prevented efe9106 landing in the
first place.
