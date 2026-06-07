# WU8.6 — corpus no-stubs + clause-diff expansion

*Last revised: 2026-06-07.*
*Path: journal/260607-corpus-no-stubs.md.*

**Branch:** `worktree-wu86-corpus-no-stubs`. Direct fast-forward into `main`
per the WU8.4 cadence.

- Spec: [`docs/superpowers/specs/2026-06-07-corpus-no-stubs-and-diff-expansion-design.md`](../docs/superpowers/specs/2026-06-07-corpus-no-stubs-and-diff-expansion-design.md)
- Plan: [`docs/superpowers/plans/2026-06-07-corpus-no-stubs-and-diff-expansion.md`](../docs/superpowers/plans/2026-06-07-corpus-no-stubs-and-diff-expansion.md)

## 1. What landed

14 commits. Closes the two demo-blocking gaps: every document a demo user
can list now resolves to real parsed clauses (no version-less stubs in the
viewer), and the demo's change-event coverage now spans all four kinds on
the UK side and three of four on the EU side.

### 1.1 Seed library changes

`packages/horizons-ingestion/src/horizons_ingestion/seed.py`:

- `run_seed` gains `samples_dir: Path | None` and `skip_v1_for: set[str] | None`.
  When `samples_dir` is set, every **freshly-inserted** document is paired
  with its on-disk v1 markdown: one `document_versions` row + parsed
  `clauses`, and `document_poll_schedule.next_poll_at` is parked at
  `2026-12-31` so the worker can't claim the staged row.
- New helpers: `compute_v1_staging_payload` + `V1StagingPayload` (pure;
  parses + builds the row payload), `_insert_v1_only` (writes the version
  + clauses + parks the schedule), `_fixture_iso_for` (resolves the ISO
  prefix used to find the markdown on disk).
- `skip_v1_for` carve-out: docs paired with a `data/samples/synthetic_v2/*.md`
  fixture skip v1 staging in `run_seed` because `stage_synthetic_v2`
  handles them atomically. Without the carve-out, `stage_synthetic_v2`'s
  "already has versions" idempotency silently skips every paired doc and
  the v2 set never lands. This integration concern surfaced during Task 2
  code review and was folded into Task 3.
- Per-doc failure tolerance: missing fixture entry, missing v1 markdown,
  or parser exception emits a warning and continues — no transaction
  rollback. Five new counters on `SeedResult` distinguish the skip
  reasons.
- v1-staging gate is on freshly-inserted rows only — re-running the
  seeder against an already-seeded DB does not re-stage v1 for documents
  that already have versions.

`scripts/seed_curated_set.py`: builds `skip_v1_for` unconditionally from
the synthetic_v2 inventory (not gated on `--stage-synthetic-v2`); passes
`samples_dir` + `skip_v1_for` into `run_seed`; prints the new counters.

### 1.2 New synthetic v2 fixtures

Three new pairs under `data/samples/synthetic_v2/`. Each was authored under
the spec's rules: single change-unit per edit, tonal mimicry of the v1,
no introduced terminology, no cross-reference breakage.

- `ie-27732019-v2.md` — UK demo-visible. **MOVED** (section 11 renumbered
  to 11A; clause body byte-identical, parser path changes from
  `('PART 2', '11.')` to `('PART 2', '11A.')`) + **MODIFIED** (section
  12(5A)(a) Ministerial order-making power upper bound widened from
  "not more than 12 weeks" to "not more than 16 weeks"). Framing: Law
  Reform Commission Revised Acts edition restoring engrossed-bill
  numbering after a gazette misprint. **The demo's only MOVED change
  event.**
- `au-2145602-v2.md` — UK demo-visible. **REMOVED** (sub-paragraph (iv)
  of the `major damage` (residence) definition: "sewage contamination of
  the interior of the residence; or") + **ADDED** (new closing sentence
  in Schedule 1 pinning the LGA boundary reference date to 4 March 2025).
  Framing: subsequent ministerial determination tightening LGA reference
  date.
- `eu-31366184-v2.md` — EU demo-visible. **MODIFIED** ("10 June 2026" →
  "17 June 2026" debriefing date) + **REMOVED** (closing paragraph of
  "Registration and engagement" on livestream + Q&A chat). Framing:
  BEREC postpones the debriefing by a week and reverts to in-person-only
  format.

`data/samples/synthetic_v2/README.md` updated with lead paragraph,
inventory, and per-pair diff-intent sections. `data/curated_set.yaml`
annotated to mark the three UK/EU docs that now have synthetic v2 pairs.

### 1.3 e2e regression case

`packages/horizons-webapp/e2e/documents-viewer.spec.ts` gains a case that
asserts every document visible to the UK demo user renders ≥1 parsed
clause card. This is the structural guard against the WU8.5-era bug where
listable docs had no `document_versions` row and the viewer fell back to
the empty state.

## 2. Demo diff coverage after this WU

- **UK demo user** (10 docs): **3 docs with diffs** — GB 28914588
  (ADDED + REMOVED + MODIFIED, existing), IE 27732019 (MOVED + MODIFIED,
  new), AU 2145602 (ADDED + REMOVED, new). All four change kinds
  represented.
- **EU demo user** (10 docs): **4 docs with diffs** — DE 20951816,
  FR 31702142, IT 26863 (existing), EU 31366184 (new, MODIFIED + REMOVED).
  Three change kinds covered; MOVED is intentionally not present on the
  EU side — adding one would require touching an already-reviewed
  fixture and isn't worth the regression risk per the spec.

After `seed_curated_set.py --stage-synthetic-v2` dry-run: 31 documents,
8 synthetic v2 pairs, 2582 clauses, 38 change events.

## 3. Notes for the lawyer reviewer

Interpretive choices to flag (not bugs):

1. **IE 27732019 MOVED framing is the thinnest.** Real Oireachtas drafting
   tradition tends to footnote misprints rather than renumber. Section 11
   → 11A is technically a valid MOVED at the parser level but the
   "Revised Acts edition restores engrossed numbering" story is
   plausible-adjacent rather than rock-solid. Fallback narrative: it's a
   label-only change in a revised consolidated text, not a substantive
   amendment. The MOVED kind matters most for the demo's "all four kinds
   covered" claim; the framing matters less.
2. **AU 2145602 REMOVED is a substantive policy narrowing.** Stripping
   sub-paragraph (iv) ("sewage contamination") from the `major damage`
   definition isn't pure drafting clarity — it narrows AGDRP
   eligibility. The framing comment calls it a clarity edit, which is
   defensible since limbs (i) interior damage and (ii) structurally-unsound
   cover most sewage-contamination cases, but a careful AGDRP claims
   officer would notice the narrower scope.
3. **EU BEREC v2 is the least sensitive.** Press-item revisions like this
   happen routinely; the date shift + format-change combo is unremarkable
   institutional behaviour.

Any pair can be reverted independently — the rest of the WU still lands.

## 4. Open punch-list items (post-demo)

1. **MOVED rendering e2e.** The new `documents-viewer.spec.ts` case
   asserts every visible document renders ≥1 clause card. MOVED
   rendering itself is covered by Vitest `ChangesView.spec.ts:124`
   (toggle wiring) + `ChangeTypePill.spec.ts` (pill) +
   `login-and-scope.spec.ts` (asserts MOVED suppressed by default). A
   dedicated e2e that toggles "Show MOVED" on and asserts the row + path
   lozenge renders would close the gap. Not gated on synthetic_v2
   staging (`seed_e2e.py` already emits a UK MOVED change_event
   directly).
2. **`stage_synthetic_v2` could close out v1's `valid_to`.** Today
   `_insert_v1_only` writes `valid_to=None` (live). If `stage_synthetic_v2`
   ever staged a v2 for the same doc (it doesn't, per the `skip_v1_for`
   carve-out), v1's `valid_to` would not be closed out. Inert under the
   current carve-out; revisit only if the data model changes to let both
   paths touch the same doc.
3. **Pyright `reportUnknownArgumentType` on `scripts/seed_curated_set.py`
   line 161.** `fixtures_doc.get("fixtures", [])` returns `Any` from
   `json.loads`. Pre-existing; independent of this WU.

## 5. Re-seeding the staging corpus

The deployed `horizons-nonprod` corpus is still WU8.5-shape. To pick up
the staged v1s + synthetic v2 pairs, dispatch the existing Job:

```
scripts/reseed_aca.sh --yes
```

Runbook: [`docs/runbooks/reseed.md`](../docs/runbooks/reseed.md). Operator
step required.

## 6. Validation summary

- 347 Python unit tests pass, 4 skipped (alignment fixture cases —
  pre-existing).
- 260 integration tests pass against testcontainers Postgres.
- 193 webapp Vitest tests pass.
- ruff + ruff-format + pyright clean.
- pre-commit clean.
- All 8 synthetic v2 pairs parse + align in `--dry-run --stage-synthetic-v2`
  (2582 clauses, 38 change events).

## 7. File-by-file deltas

- `packages/horizons-ingestion/src/horizons_ingestion/seed.py` —
  `compute_v1_staging_payload`, `V1StagingPayload`, `_insert_v1_only`,
  `_fixture_iso_for`, `run_seed` signature + body extension, `SeedResult`
  +5 counters.
- `packages/horizons-ingestion/tests/test_seed_helpers.py` — 2 new unit
  tests.
- `tests/integration/test_seed_curated_set.py` — 4 new integration tests.
- `scripts/seed_curated_set.py` — passes `samples_dir` + `skip_v1_for`;
  prints new counters.
- `data/samples/synthetic_v2/ie-27732019-v2.md` — new file.
- `data/samples/synthetic_v2/au-2145602-v2.md` — new file.
- `data/samples/synthetic_v2/eu-31366184-v2.md` — new file.
- `data/samples/synthetic_v2/README.md` — updated lead paragraph,
  inventory, 3 new diff-intent sections.
- `data/curated_set.yaml` — annotated 3 entries.
- `packages/horizons-webapp/e2e/documents-viewer.spec.ts` — new test case.

## 8. Commit ledger

```
db1641a test(e2e): assert every visible document renders parsed clauses
4ba2d98 docs(curated): annotate which UK/EU docs now have synthetic v2 pairs
8b2fa4f docs(samples): document three new synthetic v2 pairs
0a32133 feat(samples): EU 31366184 synthetic v2 (MODIFIED + REMOVED)
d055293 feat(samples): AU 2145602 synthetic v2 (ADDED + REMOVED)
e4144bc feat(samples): IE 27732019 synthetic v2 (MOVED + MODIFIED)
d6bd278 feat(seed-cli): seed_curated_set.py passes samples_dir + skip_v1_for
802fe88 test(seed): tighten _selective_raise stub return type to Clause
8ad3bd5 test(seed): integration coverage for run_seed v1 staging
5b9cffb fix(seed): gate v1-staging block on freshly-inserted documents only
8cfc405 feat(seed): run_seed stages v1 clauses + parks the poll schedule
3734729 feat(seed): add _insert_v1_only helper for v1-only fixture staging
3f32108 test(seed): tighten content_sha256 assertion to exact digest match
4425f7a refactor(seed): extract compute_v1_staging_payload helper
```
