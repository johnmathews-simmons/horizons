# Migrations runbook — expand-contract policy

*Last revised: 2026-06-05.*
*Path: docs/runbooks/migrations.md.*

*Audience: anyone writing or reviewing an Alembic migration in this repo.
Source-of-truth for the deploy-safety rules.*

## Why this exists

Migrations and code deploys happen in two separate steps that the
deploy pipeline (`deploy.yml`, WU6.3) coordinates:

```
build image  →  start migration Job (alembic upgrade head)  →  shift traffic to new revision
```

Between "migration applied" and "100% traffic on the new revision",
some requests still hit the *old* code against the *new* schema.
Anything the new schema breaks for the old code shows up as a 5xx in
that window. Expand-contract is the rule that prevents this: every
schema change is shaped so the old code still works after the
migration.

The same logic applies to rollbacks. A revision rollback flips
traffic back to the previous code; the migration is **not** rolled
back automatically. If the new schema breaks the previous code, the
rollback is broken.

## The expand-contract sequence

Apply every schema change as a chain of small, individually-safe
migrations. The shape:

1. **Expand.** Add the new shape *non-destructively* — `ADD COLUMN`
   with a nullable column, `CREATE INDEX` concurrently, `CREATE
   TABLE`, `CREATE TYPE`. The old code keeps working because nothing
   it relied on changed.
2. **Deploy the expansion.** Ship the migration to production behind
   the existing API revision. Old code still serves traffic and is
   unaware of the new shape.
3. **Backfill.** Populate the new column or table for existing rows.
   Backfills run as a separate migration (idempotent) or as a
   one-shot data-script Job. They MUST be tolerant of partial
   completion — assume the runtime might fail mid-way.
4. **Deploy the code that uses the new shape.** The next code
   revision can now read/write the new column. Old code is still
   running until traffic flips.
5. **Contract.** Once no live revision references the old shape,
   drop it in a follow-up migration — `DROP COLUMN`, `DROP TABLE`,
   `ALTER COLUMN ... DROP DEFAULT`. This is the only step that's
   irreversible from the previous revision's point of view; never
   pair it with a code change in the same deploy.

The contract step ships in **the deploy after** the last revision
that referenced the old shape stops receiving traffic. If you're
unsure whether anything still references the column, leave it
nullable and unused for one extra deploy cycle. The cost of a stale
column is zero; the cost of a contract-too-early is a 5xx storm.

### A migration that *adds* a constraint

`NOT NULL` is the trap. Adding `NOT NULL` to a populated column is a
table-rewrite + a code-breaking change in one step. Sequence it the
same way:

1. **Expand:** add a `CHECK (col IS NOT NULL) NOT VALID` constraint
   — it applies only to *new* writes, no table rewrite.
2. **Backfill** any rows where the column is NULL.
3. **Validate:** `ALTER TABLE ... VALIDATE CONSTRAINT ...` — full
   scan but no rewrite, no exclusive lock.
4. **Promote** to `NOT NULL` and drop the now-redundant CHECK
   constraint in a follow-up migration.

The same logic applies to `UNIQUE` (use `CREATE UNIQUE INDEX
CONCURRENTLY` then `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX`)
and to foreign keys (use `ADD CONSTRAINT ... NOT VALID` then
`VALIDATE CONSTRAINT` in a follow-up).

## RLS policy changes ship one deploy ahead of the code

Policies are the most common expand-contract trap because their
"shape" is the SQL of `USING` / `WITH CHECK`, which lives inside a
migration — and they apply *immediately* to every connection that
opens a transaction after the migration commits.

The rule: **a policy change that the new code depends on ships in
the deploy *before* the code change**, never in the same deploy.
Otherwise, between "migration applied" and "100% traffic on new
revision", the old code is querying under the new policy — and any
query that the new policy now rejects will 5xx until the traffic
shift completes.

The mirror also applies to relaxing policies. **A policy change
that the *old* code depends on staying restrictive ships in the
deploy *after* the code change**, never before. Otherwise, the
old code briefly runs under a more-permissive policy and can
expose rows it shouldn't.

In summary:

| Policy change tightens reads… | Ships in the deploy **before** the code change. |
| Policy change loosens reads…  | Ships in the deploy **after** the code change.  |
| Policy change adds a new role | Always safe to ship early.                      |
| Policy change drops a policy  | Treat as a contract step — last.                |

Add-only changes (new policy on a new column the new code introduces)
are an exception to the "ship before" rule because no in-flight code
queries the new column — they ship in the same deploy as the
expansion.

## Worked example — adding a `priority` column to `watchlists`

Setup: the API team wants to let users mark some `watchlists` rows
as high-priority. Schema change: add an `INT` column `priority` with
default 0, NOT NULL after backfill, plus an RLS policy that lets a
user see their own priorities only (mirrors the existing watchlist
ownership shape).

The work spans **three deploys**:

### Deploy N — expansion (migration `00NN_add_watchlists_priority_nullable.py`)

Add the column nullable, no default change to existing rows. Add the
new RLS policy. The existing API code (which doesn't know about
`priority`) keeps reading and writing the table as before; the new
column is `NULL` everywhere.

```python
def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE watchlists
            ADD COLUMN priority INT;
        """
    )
    # New policy: same shape as the existing watchlists_owner_select
    # (see migrations/versions/0005_rls_spine.py for the pattern —
    # raw `CREATE POLICY` via op.execute, scoped to api_app, USING
    # `user_id = current_setting('app.user_id')::uuid`).
    op.execute(
        """
        CREATE POLICY watchlists_owner_priority_select
            ON watchlists
            FOR SELECT
            TO api_app
            USING (user_id = current_setting('app.user_id')::uuid);
        """
    )
```

The locked-in plan (§2) names `alembic_utils.PGPolicy` as the target
shape for policies. The current migration tree uses raw
`op.execute()` (see `0005_rls_spine.py`); when the PGPolicy
adoption lands, the policy block above becomes a single
`PGPolicy(...)` declaration with the same `USING` clause. The
shipping order rule below is identical either way.

Deploy. Wait for the migration job to finish; the API revision is
unchanged.

### Deploy N+1 — backfill + code change

Two migrations and one code change ship together:

- `00NN+1_backfill_watchlists_priority.py` — set `priority = 0` for
  every row where it's currently NULL. Idempotent (`WHERE priority
  IS NULL`).
- `00NN+2_watchlists_priority_not_null.py` — add a `NOT VALID` CHECK,
  validate, set NOT NULL, drop the CHECK. Three small steps in one
  migration; each is safe to retry.
- API code in `packages/horizons-api/` learns to read and accept the
  new field. The repository layer's `WatchlistsRepository.create`
  takes an optional `priority` arg with a default of 0.

The new code runs under the new policy. The old code (briefly serving
traffic during the shift) sees the column as NOT NULL with a default
of 0 — its existing INSERTs (which don't mention `priority`) still
work because Postgres fills the default.

### Deploy N+2 — nothing to do

The column is in steady state. No contract step because nothing was
removed. Done.

### Counter-example — same change, sequenced wrong

If deploys N and N+1 are merged into a single deploy:

- Migration job applies the NOT-NULL constraint *before* the new
  code rolls out.
- The old code's `INSERT INTO watchlists (...)` (which doesn't list
  `priority`) breaks: the column default isn't picked up unless the
  ORM lets Postgres pick it, and many ORMs explicitly send `priority
  = NULL` for unset columns. Result: 5xx on every watchlist create
  during the traffic-shift window.
- Even if the old code happens to work, the new RLS policy applies
  to every connection — including the old revision's transactions
  — the instant the migration commits. If the policy's `USING`
  clause depends on a GUC the old code didn't set, the old code's
  SELECTs return zero rows for the window. The user sees an empty
  watchlist, refreshes, sees a populated one, refreshes again, sees
  empty — exactly the kind of intermittent failure that's hardest
  to debug.

Two separate deploys is the cheap fix.

## When you can skip expand-contract

You can ship a single-deploy schema change *only* when every one of
these is true:

- The table is empty in every live environment. (Empty-table NOT
  NULL is free.)
- The table has no RLS policies, or the policies aren't affected by
  the change.
- No existing code path reads or writes the affected columns.

Most "I just want to add an index" changes qualify. `CREATE INDEX
CONCURRENTLY` is still required if the table is non-empty —
otherwise the migration takes an exclusive lock and the migration
Job's `replicaTimeout: 600` clock starts ticking against every
in-flight transaction.

## Migration safety checklist

Cross-check this list before opening a migration PR. The PR template
links to this section.

- [ ] Does this migration ship a column add / type change / NOT NULL
      promotion? If yes, is the *contract* step in a separate
      migration (and ideally a separate deploy)?
- [ ] Does this migration change an RLS policy? If yes, does the
      change tighten or loosen reads, and does the deploy order
      match the rule above?
- [ ] Will the previous revision's code still work after this
      migration applies? (If you can't answer "yes" with confidence,
      split the migration.)
- [ ] Is every step idempotent / re-runnable from any partial state?
      (Migrations Job runs without retries; partial-failure leaves
      the DB in whatever shape the failing statement left it.)
- [ ] Does the migration take an exclusive lock anywhere? (Backfills
      against a large table, `CREATE INDEX` without `CONCURRENTLY`,
      `ALTER TABLE ... SET NOT NULL` on a populated column without
      the `NOT VALID` two-step.)
- [ ] Does any post-migration code path read a column / policy /
      table that was just dropped? (If yes, the contract step is
      one deploy too early.)

## Related references

- Locked-in plan §10 (deploy pipeline shape) and §2 (Alembic +
  `alembic_utils.PGPolicy` future direction).
- `packages/horizons-core/migrations/versions/0005_rls_spine.py` —
  the canonical example of how RLS policies are declared in the
  current raw-SQL pattern.
- `packages/horizons-core/migrations/versions/0009_watchlist_documents_and_scope_trigger.py`
  — already-applied migration that calls out the two-step pattern in
  its own comment when the column is added against an empty table.
- `packages/horizons-core/src/horizons_core/db/rls.md` — the
  two-axis isolation model the policies enforce.
- `infra/modules/migration-job.bicep` — the ACA Job that runs the
  tree from `deploy.yml`.
