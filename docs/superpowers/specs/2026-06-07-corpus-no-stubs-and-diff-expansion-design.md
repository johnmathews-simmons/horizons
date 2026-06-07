# Corpus: no stubs, expanded clause-diff coverage

*Last revised: 2026-06-07.*
*Path: docs/superpowers/specs/2026-06-07-corpus-no-stubs-and-diff-expansion-design.md.*

**Date:** 2026-06-07
**Status:** approved (brainstorming), ready for implementation plan.
**Successor of:** [`2026-06-06-wu85-demo-corpus-and-doc-viewer-design.md`](./2026-06-06-wu85-demo-corpus-and-doc-viewer-design.md).
**Related journal:** [`260606-stale-revision-and-reseed-teardown.md`](../../../journal/260606-stale-revision-and-reseed-teardown.md), [`260606-wu85-demo-corpus-and-doc-viewer.md`](../../../journal/260606-wu85-demo-corpus-and-doc-viewer.md).

## Problem

WU8.5 expanded the curated set to 31 documents and added the documents viewer, but only 5 fixtures have full content (`document_versions` + `clauses` + `change_events`): GB 28914588, DE 20951816, FR 31702142, IE 8064194, IT 26863 — the synthetic-v2 pairs staged from `data/samples/synthetic_v2/`.

The remaining 26 are stubs: `documents` + `document_poll_schedule` rows only. They were meant to be filled by the ingestion worker fetching v1 content from Lawstronaut, but two things block that path: (a) the worker is not claiming rows in staging (root cause not diagnosed; deferred per the deploy-pipeline journal), and (b) Lawstronaut `/v2/content/{id}/{version}` has returned `200 + empty data` for these IDs (open question in `lawstronaut-api-key-facts`).

The user-visible effect today:

- **demo-uk@demo.example.com** (UK / BANKING) sees 10 docs in the list; only 1 (GB 28914588) renders parsed clauses. The other 9 sit on "Loading clauses…" with no version label.
- **demo-eu@demo.example.com** (EU / BANKING) sees 11 docs; only 3 (DE, FR, IT) render clauses.
- Clause-level diffs — the headline product beat — are reachable from 1 doc in the UK list and 3 in the EU list.

For 2026-06-08 the demo needs every doc in each user's list to look complete, with parsed clause structure visible and the four change kinds (ADDED, REMOVED, MODIFIED, MOVED) reachable across the corpus at lawyer-defensible quality.

## Goal

1. **No stubs in either demo user's documents list.** Every doc in scope renders with parsed clauses and a version label.
2. **Clause structure is the visible substrate.** Every doc, when the `Show clause structure` toggle is on, shows the parser-assigned anchor path (e.g. `PART_2/SECTION_4/(a)/(i)`).
3. **Clause-level diffs across the corpus.** Both demo users can browse to multiple docs that have a v1 → v2 change set, and the demo can point at each of ADDED / REMOVED / MODIFIED / MOVED.
4. **Every authored edit is lawyer-defensible.** New synthetic v2 deltas are limited to English-language source documents where I can produce tone, register, and legal-style consistent with the v1.

## Non-goals

- Fixing the ingestion worker's claim loop. Tracked in the post-demo punch list (`260606-deploy-pipeline-end-to-end.md`).
- Hand-authoring v2 deltas in Greek, Finnish, Danish, Catalan, Georgian, Chinese, Arabic, or Portuguese. The lawyer-review bar makes this unreliable for languages outside the author's working set.
- Reconsidering the BANKING-only subscription scopes for the demo accounts. Editorial-honest sector mix stays sacrificed for visible list density, as decided in WU8.5.
- Richer markdown rendering than plain-text-in-`<pre>`. Out of scope per WU8.5.
- Adding new clauses to `data/curated_set.yaml`. The 31-doc set stays.

## Approach

Two parallel changes, joined by a single re-seed step.

### A. Stage v1 clauses for every curated document on seed

The current seed library has two write paths:

- `run_seed(...)` — for every matched fixture: insert `documents` + `document_poll_schedule`.
- `stage_synthetic_v2(...)` — for paired fixtures only: parse v1 + v2 markdown, write `document_versions`, `clauses`, and `change_events`.

Add a third path that runs unconditionally as part of `run_seed`: for every matched fixture, also parse its `data/samples/<iso>-<id>-v1.md`, write one v1 row in `document_versions`, and write the parsed `clauses`. Idempotent: skip if a `document_versions` row already exists for `(document_id, version_label='v1')`.

Park `document_poll_schedule.next_poll_at = 2026-12-31T00:00:00Z` for every seeded doc — same trick `stage_synthetic_v2` already uses for v2 pairs. The worker can't claim a doc parked in the future, so any post-fix worker run won't overwrite the deterministic staged content during the demo window.

`--stage-synthetic-v2` still controls whether the additional v2 + alignment path runs. The two are independent: a doc gets v1 always, and v2 + change events only if a `<iso>-<id>-v2.md` exists under `synthetic_v2/`.

The `<iso>` token in the `<iso>-<id>-v1.md` filename is the *capture* ISO (from `fixtures.json`), not the seeded jurisdiction. The seed library already handles that mapping via the `jurisdiction:` override in `curated_set.yaml`.

#### Failure modes to design for

- **Parser failure on a fixture.** Some non-English fixtures may stress the parser (e.g. CZ 29662776 has no markdown headings; clause structure is encoded inline as `ČÁST PRVNÍ` / `Čl. I`). If the parser raises or returns zero clauses, the seed must log a warning and skip the v1 staging for that fixture (insert `documents` + schedule, no `document_versions`) rather than abort the whole seed. Behaviour matches today's "stub" outcome for that one doc — failure is contained.
- **Re-seed idempotency.** The reseed Job wipes the corpus tables before re-running the seed. With v1 staging in `run_seed`, the wipe-then-reseed cycle will re-create everything. Verify the wipe order in `scripts/reseed_corpus.py` deletes `change_events` → `clauses` → `document_versions` → `documents` (FK order) before re-running the seed.

### B. Author 3 new English-language synthetic v2 pairs

Existing 5 v2s (GB, DE, FR, IE, IT) stay as-is. Each carries a 3-edit delta (ADDED + REMOVED + MODIFIED) per `data/samples/synthetic_v2/README.md`.

New v2s, all English-source. Each carries a 3–4-edit delta:

| Slug | Demo visibility | Diff kinds | Edits |
|------|-----------------|------------|-------|
| `gb-28914588-v2.md` (existing, untouched) | UK | ADDED + REMOVED + MODIFIED | No change. Already reviewed; not re-opened. |
| `ie-27732019-v2.md` (new) | UK | MOVED + MODIFIED | Move one sub-section to a different parent (clause text unchanged, parser anchor path changes), and amend a single numeric threshold (a fee, percentage, or date). Conservative Oireachtas-amendment style. The MOVED edit is what carries the fourth change kind across the whole demo corpus. |
| `au-2145602-v2.md` (new) | UK | ADDED + REMOVED | Add a new short clause; remove a clearly deprecated paragraph. |
| `eu-31366184-v2.md` (new) | EU | MODIFIED + REMOVED | Change a date reference; remove a paragraph that is clearly time-bounded. |

Final coverage after this change:

- **UK** — 3 docs with diffs out of 10 (GB, IE, AU). Across them: ADDED, REMOVED, MODIFIED, MOVED all represented.
- **EU** — 4 docs with diffs out of 11 (DE, FR, IT, EU-BEREC). Across them: ADDED, REMOVED, MODIFIED covered. MOVED is *not* represented on the EU side; the demo will tell the MOVED story via IE in the UK list. Adding MOVED to the EU side would require touching an already-reviewed fixture (DE/FR/IT) — not worth the regression risk.

#### Lawyer-defensibility rules for each new edit

- **Single, small unit per edit.** A clause boundary, a numeric value, a date, a single paragraph. Never rewrite paragraphs.
- **Tone, register, and structural mimicry.** The new clause text reads as continuation of the v1's voice. No introduced terminology, no new style.
- **Plausible amendment shape.** For Acts (IE 27732019), use the language of statutory amendment ("section X is amended by the substitution for…"). For tribunal judgments (GB 28914588), edits are post-hoc corrigenda. For press items (EU 31366184), edits are editorial revisions and date corrections.
- **Self-review pass.** Before committing each v2, re-read the v1 + v2 in `diff` form and confirm: (a) the edit is locally consistent; (b) no inadvertent edits leaked outside the intended deltas; (c) the parser produces a clean diff (no spurious moves from re-flowed whitespace).

#### Documentation

`data/samples/synthetic_v2/README.md` gains a row per new v2 in the inventory table, with a diff-intent block matching the existing pattern.

### Joining step — re-seed staging

After both A and B land, `scripts/reseed_aca.sh --yes` (laptop wrapper for the `horizons-dev-reseed-corpus` ACA Job) wipes and re-seeds the staging corpus. The Job will run with the latest worker image that contains the new seed library code. This is the existing operator workflow; no new tooling needed.

Pre-flight: confirm the Job's image is up-to-date. The deploy-pipeline punch list notes Job images currently go stale on image-only pushes (the Bicep step is skipped when no `infra/` files change). Workaround: manually bump the Job image to the latest pushed worker SHA with `az containerapp job update --image` before invoking `reseed_aca.sh`.

## Architecture

### Data flow

```
data/curated_set.yaml ─┐
data/samples/*.md      ├─► seed library (parse_curated_set + run_seed)
fixtures.json          ─┘            │
                                     ▼
                          (per fixture in scope)
                                     │
                  ┌──────────────────┴──────────────────┐
                  ▼                                     ▼
       documents + document_poll_schedule    document_versions (v1)
       (next_poll_at = 2026-12-31)             + clauses (parsed)
                                                       │
                                            (if synthetic_v2 sibling)
                                                       ▼
                                        document_versions (v2)
                                        + clauses (parsed)
                                        + change_events (aligned)
```

### Components touched

- `packages/horizons-ingestion/src/horizons_ingestion/seed.py` — `run_seed` gains a v1-staging step. Probably extracts a `_stage_v1(...)` helper that `stage_synthetic_v2` can reuse for its v1-side write rather than re-implementing.
- `packages/horizons-ingestion/tests/test_seed.py` — new unit cases for: a curated doc with a v1 fixture on disk produces `document_versions` + `clauses`; parser failure logs warning and continues; idempotent re-run inserts nothing.
- `data/samples/synthetic_v2/ie-27732019-v2.md`, `au-2145602-v2.md`, `eu-31366184-v2.md` — new fixtures.
- `data/samples/synthetic_v2/gb-28914588-v2.md` — append one MOVED edit.
- `data/samples/synthetic_v2/README.md` — inventory + diff-intent updates.
- `packages/horizons-webapp/e2e/documents-viewer.spec.ts` — assert UK demo's documents list renders parsed clauses for every visible doc, not just GB.
- `packages/horizons-webapp/e2e/` (changes spec, if separate) — assert at least one MOVED event renders with `before → after` path lozenge.

### What does NOT change

- `data/curated_set.yaml` — 31-doc set, BANKING-only relabels.
- `scripts/seed_curated_set.py` CLI surface — same flags, same arguments. Behaviour silently strengthens.
- `documents` table schema or the append-only trigger.
- The clause-tree-parser. We assume the parser already handles every English fixture in the curated set; failures fall back to stub behaviour for that one doc.
- `/v1/documents` router and handlers. The webapp already does the right thing when a doc has parsed clauses; the data is the only gap.

## Testing

### Unit (Python)

- `test_run_seed_stages_v1_clauses` — a fixture in scope with a v1 markdown on disk produces one `document_versions` row and `clauses_inserted > 0`.
- `test_run_seed_parser_failure_does_not_abort` — patch the parser to raise; assert `documents` and schedule still insert; assert a warning is emitted naming the document; assert no `document_versions` row.
- `test_run_seed_v1_idempotent` — running twice in a row stages once; second pass reports skipped.
- `test_run_seed_v1_then_synthetic_v2` — `run_seed` followed by `stage_synthetic_v2` produces the expected pair without duplicate v1 writes.

### Integration (Python)

- `test_seed_curated_set_full_corpus` (extension) — run end-to-end against a testcontainers Postgres; assert every curated, in-scope fixture has `document_versions.label = 'v1'`; assert `clauses` row count per doc matches what the parser would emit for that fixture on disk.

### E2E (Playwright)

- `documents-viewer.spec.ts` — log in as UK demo; navigate to `/documents`; assert list has ≥10 entries; for each visible row click through and assert `ClauseOverlay` renders ≥1 clause (no "Loading clauses…" state stuck).
- `changes-viewer.spec.ts` (existing or extended) — assert a MOVED event renders with the before/after path lozenge correctly bridged.

### Manual lawyer review (out-of-band)

Before merging, diff each new v2 against its v1 and re-read in legal-style context. Capture the review in a journal entry (`260607-corpus-no-stubs.md`) noting which docs were reviewed, by whom, and any deferred concerns.

## Risk and mitigation

| Risk | Mitigation |
|------|------------|
| Parser fails on an English fixture (e.g. AU 2145602 has unusual structure). | Per-doc warning + skip-v1, continue seeding. Demo loses clause view for that one doc; everything else stays correct. Caught by integration test running against all fixtures. |
| A lawyer reading the demo flags a tonal regression in a new v2. | Single-edit-unit rule + self-review pass. Deltas are small enough that any flagged edit can be reverted to v1 text without touching the rest of the v2. |
| Re-seed Job runs with stale worker image and uses old seed code. | Pre-flight `az containerapp job update --image` to the latest worker SHA before invoking `reseed_aca.sh`. Documented as a step in the implementation plan, not a code change. |
| Worker resumes claiming during the demo and overwrites staged content. | `next_poll_at = 2026-12-31` on every seeded doc parks the claim loop. The worker is also currently not claiming — double mitigation. |
| Reseed teardown order drops a constraint and breaks the wipe. | Verify the wipe SQL in `scripts/reseed_corpus.py` includes `change_events` and `clauses` deletes in FK-correct order. If not, add them. |

## Rollout

1. Land the v1 staging change behind the existing seed CLI; no flag flip needed.
2. Land the new synthetic v2 files in the same PR or a follow-up.
3. Run the local laptop boot (`docs/runbooks/local-dev.md`) + manual click-through as the UK and EU demo users to confirm both lists render full content.
4. Bump the Job image to the latest worker SHA (operator step).
5. Run `scripts/reseed_aca.sh --yes` against staging.
6. Open the deployed SPA as UK and EU demo users; confirm the full list renders, then walk the changes view.

## Open questions

None. Resolved during spec self-review: MOVED is carried by the new IE 27732019 v2 only; existing v2s are not re-opened; EU-side MOVED coverage is intentionally absent.

## Successor work (deferred)

- Worker claim-loop diagnosis. Punch-list item.
- Non-English v2 deltas at lawyer quality. Needs a per-language reviewer; out of scope for 2026-06-08.
- Replacing the `git diff HEAD~1 HEAD` baseline in `deploy.yml` so Bicep stops getting skipped on batched pushes. Punch-list item.
