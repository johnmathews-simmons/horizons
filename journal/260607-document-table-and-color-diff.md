# 2026-06-07 — Document-pivot + colour-coded diff view

*Last revised: 2026-06-07.*
*Path: journal/260607-document-table-and-color-diff.md.*

## 1. What landed

Two webapp UX changes prompted by demo prep:

1. **Card → documents table.** Clicking a jurisdiction or sector card on the homepage dashboard now lands on a paginated 8-column document table (Name | Length | Added | Removed | Modified | Moved | Previous version | Current version) rather than the flat recent-changes list. Page size 25, prev/next with URL-synced `offset`.
2. **All-changes colour-coded diff.** The document split view now colours every clause-level change between v1 and v2 (ADDED green, REMOVED red, MODIFIED amber, MOVED blue) with a small pill labelling the type. The `?before=` / `?after=` deep-link query string from the recent-changes list still works but now drives scroll-into-view only — the colouring comes from the full change-event set for the document.

Backend: `/v1/documents` (list + detail) now returns four per-document aggregate fields on every row: `clause_count`, `change_counts: {added, removed, modified, moved}`, `previous_version_at`, `current_version_at`. One bounded SQL aggregate per page (≤50 rows), written as a SQLAlchemy ORM CTE with a `row_number()` window function over `document_versions` ranked by `effective_date desc nulls last, created_at desc`.

Webapp: `ChangeTypePill` now reads its colours from the new central `src/constants/change-colors.ts`, so the legend, the corner pills inside `ClauseOverlay`, and the existing change-row pills on the Recent Changes view all stay in lock-step. Side effect: MODIFIED pills on `/changes` shifted from blue → amber and MOVED pills from slate → blue. Intended (the spec called the single source of truth out explicitly).

## 2. Process

Followed `superpowers:brainstorming` → `writing-plans` → `subagent-driven-development` end-to-end. Worktree `worktree-document-pivot-color-diff` under `.claude/worktrees/`. 13 tasks plus three small follow-ups (test-time `assert isinstance` → `raise TypeError`, lint cleanup, raw-SQL → ORM refactor).

Spec: [docs/superpowers/specs/2026-06-07-document-pivot-and-color-diff-design.md](../docs/superpowers/specs/2026-06-07-document-pivot-and-color-diff-design.md)
Plan: [docs/superpowers/plans/2026-06-07-document-pivot-and-color-diff.md](../docs/superpowers/plans/2026-06-07-document-pivot-and-color-diff.md)

## 3. Surprises / things to remember

- The architectural guard at `tests/test_raw_sql_isolation.py` catches `sqlalchemy.text(...)` outside `db/session.py`. Task 2's first cut used raw SQL for the CTE / window-function aggregate; the guard correctly fired on the integration sweep and the implementation was refactored to ORM (`func.row_number().over(...)`, `select(...).cte(...)`, `aliased(...)`, `func.sum(case((...,1), else_=0))`). Worth keeping in mind for future repo SQL.
- The pre-existing e2e in `login-and-scope.spec.ts` referenced the old `data-highlight="true"` attribute and the old `/changes?jurisdiction=UK` route — both updated as part of Task 12.
- The seed helpers in `tests/repos/test_documents_stats.py` had to use `migrated_engine` (sync, superuser) rather than `admin_session`, because `admin_bypass` is SELECT-only on corpus tables. Same pattern as `test_corpus_shape.py`.
- The plan's seed SQL skeleton omitted several NOT NULL columns (`document_versions.content_blob_container`, `content_blob_key`, `content_sha256`; `change_events.jurisdiction`, `sector`). The implementer filled them in. Worth folding into the plan template next time.

## 4. Coverage / verification

- Python: 619 passed, 4 skipped (existing nightly-only fixture skips).
- Webapp unit: 217/217 across 31 files.
- Webapp build: vue-tsc + Vite clean.
- Webapp lint: oxlint + eslint clean.
- Architectural guard test: green.
- Playwright e2e: written but NOT run locally (boot is heavy — Postgres + alembic + uvicorn + npm preview); will run on the next push to `main`.

## 5. Follow-ups for the post-demo punch list

- Optional: surface MOVED toggle on the diff view if the curated set produces visually-noisy renumbering — currently MOVED is on by default per the spec decision.
- Word-level diff inside MODIFIED clauses (out of scope here; would replace the amber box with a precise within-text highlight).
- Sortable headers on the documents table — out of scope; the demo doesn't need it.
- Verify the demo seed includes a 2-version UK doc so the new e2e test's tolerant "legend OR single-pane" assertion can be strengthened.
