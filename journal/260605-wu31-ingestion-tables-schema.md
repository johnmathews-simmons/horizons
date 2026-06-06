# WU3.1 — Ingestion tables schema

*Session 2026-06-05. Branch `worktree-eng-wu3.1-ingestion-schema` → ff-merged to `main`.*

Second unit on Track 3. WU3.0 picked the worker shape (ADR-0001 — long-running asyncio container, 50 ms SKIP LOCKED claim tick); WU3.1 lands the database surface that loop runs against. Migration `0007_ingestion_tables.py` extends `document_versions` with the validity window the per-poll transaction (WU3.4) writes, narrows that table's append-only trigger to permit `valid_to`-only updates, and adds two operator-only tables: `document_poll_schedule` (the claim-loop substrate) and `ingestion_incident` (the failure / parking log).

## What shipped

1. `packages/horizons-core/migrations/versions/0007_ingestion_tables.py` — Alembic migration. Three concerns in one revision: (a) `ALTER TABLE document_versions ADD COLUMN version_no int, valid_from timestamptz, valid_to timestamptz` (all nullable for now), `UNIQUE(document_id, version_no)`, and `idx_document_versions_doc_valid_to (document_id, valid_to DESC)` for the "current live version" lookup; (b) rewritten `reject_document_version_update()` that raises iff any column *other than `valid_to`* changed, plus `GRANT UPDATE (valid_to) ON document_versions TO ingestion_worker`; (c) `document_poll_schedule(document_id PK, cadence_interval, next_poll_at, last_polled_at, failure_count)` with `idx_document_poll_schedule_next_poll_at`, and `ingestion_incident(id bigserial, document_id, error_class, payload jsonb, occurred_at)` with two indexes (per-doc and global). Symmetric `downgrade()` restores the strict trigger and drops all additions.
2. `tests/test_ingestion_tables_migration.py` (11 integration tests) — column shape; ownership = `schema_owner`; the four new indexes; PK + FK + uniqueness; the relaxed trigger (`UPDATE valid_to` succeeds; `UPDATE content_bytes` raises; combined `SET valid_to = …, content_bytes = …` raises); table-level grants for the new tables; *column-level* grant for `ingestion_worker.UPDATE(document_versions.valid_to)` queried out of `information_schema.column_privileges`; `bigserial id` returns monotonically increasing ints; the ADR-0001 SKIP LOCKED query runs against the live schema.
3. `tests/test_corpus_tables_migration.py` — one assertion adjusted. The pre-WU3.1 test expected `ingestion_worker` table-level grants on `document_versions` to be `{SELECT, INSERT}`. That stays true after WU3.1 because the new `UPDATE` is column-scoped (to `valid_to`) and column-scoped grants live in `information_schema.column_privileges`, not `role_table_grants`. The dedicated column-grant assertion in the new test file is the canonical check for the relaxed write surface.
4. `packages/horizons-core/src/horizons_core/db/roles.md` — GRANT matrix extended with the two new tables; prose extended with a paragraph explaining the `document_versions` exception (column-scoped UPDATE for the ingestion worker, trigger as the substantive rule).
5. `packages/horizons-core/src/horizons_core/db/schema.md` — `document_versions` table gains the three new column rows, the new index, and an updated "Writes" paragraph; two new sections (`document_poll_schedule`, `ingestion_incident`) inserted between `clauses` and `admin_access_log`; the "Append-only enforcement" summary updated so the relaxed `document_versions_no_update` rule is documented alongside the strict `documents_no_update` / `clauses_no_update`; "Multi-tenant access (current state)" gains a WU3.1 paragraph stating that the two new tables are operator-only by grant (no RLS).

Cumulative-since-prior-session diff: one new migration, one new test file (315 lines), four touched docs/tests. Full sweep green: 316 tests + 4 fixture-too-small skips + 1 deselected (50.5 s wall-clock); `ruff check`, `ruff format --check`, `pyright` (0 errors / 14 stub warnings — unchanged), `pre-commit run --all-files` all clean.

## Decisions resolved up-front

Three questions pinned via `AskUserQuestion` (with previews) before the first edit, after a fourth was retired by reading existing code.

1. **Extend `document_versions` in this migration.** Considered (b) carving validity into a separate `document_version_validity(document_version_id PK, valid_from, valid_to)` table so `document_versions` could stay strictly append-only (cleaner invariant but a join on every "live version" read) and (c) splitting WU3.1 into a schedule-and-incident-only unit with the `document_versions` reshape deferred to a WU3.1b. (a) won because the design doc (`docs/RFC-4 services.md` §"Ingestion service") explicitly names `document_versions.valid_to` as the column the worker extends, and the existing trigger could be narrowed instead of rewritten around — one migration, one canonical place for version validity.
2. **No RLS on the two new tables — grants only.** Considered (b) RLS-enabled with default-deny so future client exposure would be additive. (a) won because `document_poll_schedule` and `ingestion_incident` are operator-only forever (admin reads via aggregated `/v1/admin/health/ingestion`, never raw rows for `client`); adding RLS would be ceremony without defence-in-depth payoff. `client` (= `api_app`) has zero grants on either; admin reads go through the audited path.
3. **`bigserial` ids for the corpus side.** Considered (b) UUIDv7 for cross-schema uniformity. `change_events` already uses `bigserial` for the same shape ("append-only operator-curated log keyed by an integer"); reuse keeps indexes tight and the IDs human-quotable in incident reports.

Retired before asking: **Q3 from the prompt — `documents.source_identifier` shape.** The existing `documents.lawstronaut_document_id text NOT NULL UNIQUE` (from WU1.2, migration 0003) already satisfies it. No change needed.

## Plan drift

The prompt's acceptance criteria listed `documents` and `document_versions` columns the existing 0003 schema didn't have. Surfaced this as the first AskUserQuestion option set — the discrepancy was real (the 0003 schema is closer to "document-blob bookkeeping" than to the validity-window view doc 4 needs) and the path forward had three plausible answers, none of which I should have picked unilaterally.

The other gotcha-via-existing-code was the strict append-only trigger on `document_versions` from 0003 directly conflicting with doc 4's "extend `valid_to` on every poll" semantics. Same surface as the column shape question; resolved together by choosing option (a).

## Postgres specifics learned

1. **`information_schema.role_table_grants` does NOT report column-scoped `UPDATE` grants.** Granting `UPDATE (valid_to) ON document_versions` populates `information_schema.column_privileges` but leaves no row in `role_table_grants` for that table/grantee. First-run failure of `test_per_role_grants_match_design` made this concrete. The fix: assert table-level grants from `role_table_grants` and column-level grants from `column_privileges` as two separate tests.
2. **The testcontainers Postgres fixture is session-scoped and rows persist across tests.** First-run failure of `test_claim_loop_skip_locked_query_runs` — the SKIP LOCKED query found one of `test_document_poll_schedule_pk_and_fk`'s residual rows alongside the row this test inserted. Inclusion-based assertion (`assert doc_id in {…}`) instead of equality is the right shape; the alternative (clean-DB-per-test) would 10× the test-run time.
3. **`bigserial` needs `GRANT USAGE ON SEQUENCE` for non-owner inserters.** `bigserial DEFAULT nextval('ingestion_incident_id_seq')` won't advance under the `ingestion_worker` role without `GRANT USAGE ON SEQUENCE ingestion_incident_id_seq TO ingestion_worker`. Easy to miss because `GRANT INSERT ON ingestion_incident` looks like enough.

## Why nullable for now

`version_no`, `valid_from`, `valid_to` ship nullable in this migration. The design doc treats them as load-bearing — the worker always populates them — but the codebase has no worker yet, and multiple existing test helpers (`_insert_version` in `test_repos_corpus.py`, `test_rls_corpus.py`, `tests/isolation/conftest.py`) insert versions without these columns. Adding `NOT NULL` here would force a touched-test sweep across half the repo for state that has no application code populating it.

The expand-contract migration policy (`docs/6. infra-and-deployment.md` … will, when WU6.5 lands, name this pattern explicitly) is the right shape here: add the column nullable, populate from the worker (WU3.4), tighten to `NOT NULL` in a follow-up migration once every writer is on the new code path. The migration's per-column `COMMENT ON COLUMN` documents the eventual constraint ("Nullable until WU3.4 populates it; tightened in a later migration.") so future readers don't think the nullable shape is forever.

## Trigger relaxation, in detail

```sql
CREATE OR REPLACE FUNCTION reject_document_version_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id              IS DISTINCT FROM OLD.id
       OR NEW.document_id  IS DISTINCT FROM OLD.document_id
       OR NEW.version_label IS DISTINCT FROM OLD.version_label
       OR NEW.version_no   IS DISTINCT FROM OLD.version_no
       OR NEW.publication_date IS DISTINCT FROM OLD.publication_date
       OR NEW.effective_date IS DISTINCT FROM OLD.effective_date
       OR NEW.content_blob_container IS DISTINCT FROM OLD.content_blob_container
       OR NEW.content_blob_key IS DISTINCT FROM OLD.content_blob_key
       OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
       OR NEW.content_bytes IS DISTINCT FROM OLD.content_bytes
       OR NEW.created_at   IS DISTINCT FROM OLD.created_at
       OR NEW.valid_from   IS DISTINCT FROM OLD.valid_from
    THEN
        RAISE EXCEPTION
            'document_versions is append-only except valid_to '
            '(only valid_to may change via UPDATE)';
    END IF;
    RETURN NEW;
END;
$$;
```

Enumerating every column instead of `NEW.* IS DISTINCT FROM OLD.* EXCEPT valid_to` is the safer shape — there's no such syntax, but more importantly an enumeration forces a deliberate decision when future migrations add a column. A future migration adding a column that *should* be mutable adds an `OR` branch with a flipped sense, or restructures the trigger; the explicit list is the prompt to think.

Tests prove the boundary on both sides: `UPDATE valid_to` succeeds; `UPDATE content_bytes` raises; a combined `SET valid_to = …, content_bytes = …` raises. The combined case is the load-bearing test — a careless `SET` clause that bundles a legitimate `valid_to` extension with an unrelated content change cannot slip through.

## Path to WU3.3

The schedule is the substrate for the claim loop. WU3.3 will run the SKIP LOCKED query the test in this unit smoke-asserts already parses against the live schema; the only delta will be the application-side wrapper (asyncpg connection, the 50 ms tick, the SIGTERM-drain shape from ADR-0001). WU3.4 then opens the per-document transaction that writes the new `document_versions` row, the parsed clauses, the alignment output, and the `change_events` rows — and on the "unchanged" branch, the `UPDATE valid_to` path this unit's trigger and column grant authorise.

## Sweep summary

| Check | Result |
| --- | --- |
| `uv run pytest` | 316 passed, 4 skipped (fixture too small), 1 deselected (`nightly`) — 50.52 s |
| `uv run pytest tests/test_ingestion_tables_migration.py` | 11 passed |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 75 files clean (2 reformatted in-flight, re-verified) |
| `uv run pyright` | 0 errors, 14 stub-missing warnings (unchanged) |
| `uv run pre-commit run --all-files` | All hooks passed |
| `git status` post-commit | Clean |

Branch ff-merged into `main`; remote feature branch deleted; worktree removed.

## Next session

WU3.2 (Lawstronaut client + token-refresh seam) is independent of WU3.1 (depends on WU0.3 instead). Either WU3.2 or WU3.3 is a defensible next pick; WU3.3 (the claim loop) reads the schedule shape we just shipped and the spike code that informed ADR-0001 is still warm in the repo's history. Fresh-session recommended either way — the next unit is application code in `packages/horizons-ingestion/`, a different surface from this unit's migration + docs work.
