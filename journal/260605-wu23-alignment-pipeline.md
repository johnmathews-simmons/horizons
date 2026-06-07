# WU2.3 — Alignment pipeline

*Last revised: 2026-06-06.*
*Path: journal/260605-wu23-alignment-pipeline.md.*

*Session 2026-06-05. Branch `worktree-eng-wu2.3-alignment` → ff-merged to `main`.*

Fourth Track-2 unit. Composes everything that came before: WU2.0/WU2.1's clause-tree parser produces `Clause` trees from markdown, WU2.2's similarity primitives (`shingle` / `minhash` / `jaccard` / `lsh_candidates`) score content, and `TuningConfig` carries the four runtime knobs. WU2.3 wires them into the four-pass alignment pipeline documented in `docs/RFC-2 clause-alignment.md` and emits `ChangeEvent` rows — the schema the API will eventually surface to customers. This is the algorithm the demo's "which clause changed and how" headline turns on.

## What shipped

1. `packages/horizons-core/src/horizons_core/core/alignment/align.py` — public entry point `align(v1: Clause, v2: Clause, *, tuning: TuningConfig | None = None) -> list[ChangeEvent]` plus the frozen Pydantic `ChangeEvent` model and four internal pass helpers. The file is self-contained: parser + similarity + tuning come in through the existing module exports; nothing reaches outside `horizons_core.core.alignment`.
2. `ChangeEvent` — `change_type ∈ {ADDED, REMOVED, MODIFIED, MOVED}`, optional `before/after_clause_uid`, optional `before/after_path: tuple[str, ...]`, optional `before/after_text`, and `alignment_confidence: float` validated `gt=0.0, le=1.0`. A `model_validator(mode="after")` enforces field-presence per change type: ADDED has no before-side, REMOVED no after-side, MOVED requires identical text and distinct paths, MODIFIED requires both sides and differing text. The validators are the canonical ontology — no callers can construct nonsense events.
3. `__init__.py` re-exports `ChangeEvent`, `ChangeType`, `align` alongside the existing surface; the module's `__all__` is sorted.
4. `docs/RFC-2 clause-alignment.md` — extended the *Implementation* section to anchor the four passes at `align.py`, document the heading-anchor + path-anchor split in pass 2, and write up the identity rule and MOVED-vs-MODIFIED tie-break explicitly so future readers don't relitigate them.
5. `packages/horizons-core/tests/test_align.py` (41 default-marker tests, 690 lines) — ChangeEvent validators (12 cases), identity, insert, delete, modify, swap, monotonic non-crossing constraint, boilerplate-heading guard, short-body fallback (3 cases), pass-3 LSH same-side filter (both v1- and v2-duplicate variants), pass-3 DP score-rejected branch, container-vs-leaf alignment, tuning-config plumbing, and two end-to-end IE-fixture cases. 100% line + branch on `align.py`.

Final tally: 41 default-marker tests on `align.py`, 163 horizons-core fast tests overall (was 122 before WU2.3); 100% line + branch on `align.py`; full sweep green (`uv run pytest -m "not integration"`, `ruff check`, `ruff format --check`, `pyright`, `pre-commit run --all-files`, webapp lint:check + build + vitest).

## Decisions resolved up-front

Four pinned questions, asked via `AskUserQuestion` (with previews) before any edit. Resolutions:

1. **Alignment unit = every clause with non-empty `body_text`.** Not just leaves. Containers like a Section node carrying preamble text get aligned independently from their sub-clauses, so a preamble edit and a child edit produce two distinct events. Pure structural containers (Parts with no body) are skipped naturally because they have no text to align. Path on each event is the disambiguator when parent and child both fire for the same semantic edit.
2. **`clause_uid` stubbed `None`.** The design doc calls for stable database-assigned UIDs threaded across versions, but UID assignment belongs to the ingestion version-transaction (a later unit). Deriving a UID from the heading-anchored path here would actively contradict the "identity survives renumbering" invariant the design doc spends most of its words on. The `align()` return value is the *pairing*; UID materialisation is downstream.
3. **MOVED only when text is byte-identical and path differs.** Any text drift (even whitespace-only) downgrades to MODIFIED. A clause whose path *also* changed still emits one MODIFIED event — `before_path != after_path` on the event itself carries the move signal. This keeps the change-type ontology orthogonal to the path delta and avoids fan-out (one semantic edit → one row).
4. **`align(tree, tree) → []`.** A paired clause whose path and body are both unchanged is dropped, not emitted as a phantom MOVED-at-confidence-1.0. Re-ingesting an unchanged version is a zero-row operation. The customer-facing UI already hides MOVED by default, so the visible result is the same, but the storage and event-stream cost is real.

## Plan drift — heading-anchor wasn't enough

The spec described Pass 2 as "heading-title equality + content corroboration." Implemented that way, the end-to-end IE-fixture test fired ~150 spurious `REMOVED + ADDED` events on `align(tree, tree)` for an identical tree. Cause: many IE leaf clauses (the `(a) / (b) / (i) / (ii)` sub-clauses inside Section 4) have `heading_text = None` — the parser only synthesises `heading_text` from a preceding bold heading, which these sub-clauses don't have. Without a heading, Pass 2 didn't fire; without shingles (short bodies), Pass 3 didn't fire either, and the residual classifier saw "v1 has clause X / v2 has clause X" as separate-and-distinct.

The fix: Pass 2 now considers **two anchors**, not one:

- *Heading anchor* — both clauses have a non-`None` `heading_text` and the texts are byte-equal.
- *Path anchor* — both clauses have `heading_text = None` and their `path` tuples are equal.

Mixed pairs (one side heading-bearing, one not) are excluded and flow to Pass 3. Content corroboration (jaccard ≥ threshold for long bodies, exact body equality for short bodies) is still required in both anchor variants. The path anchor is the weaker of the two — path is renumberable in principle — but for the unrenumbered case it's a strong identity signal and is necessary to keep unchanged unheaded leaves from fanning out. The doc was updated to reflect this split.

The end-to-end IE identity test (`align(tree, tree)`) is the canary that catches this — kept as a permanent regression.

## What I considered and didn't do

1. **Removing the `if side_a == side_b: continue` defensive filter from Pass 3.** The signatures list always places v1 entries before v2, so `lsh_candidates`' canonical `(lo, hi)` ordering means cross-side pairs always have `side_a == "v1"`. But same-side near-duplicate pairs (two boilerplate clauses in v1, two in v2) do surface from LSH and need filtering. Kept the check; added two targeted tests (v1-duplicates and v2-duplicates) that exercise both directions of the branch.
2. **Computing pass-2 confidence as a function of jaccard.** Considered `max(0.9, jaccard_estimate)` so a perfect-content pair nudges confidence above 0.9. Implemented it; tests pass. Lower bound is the constant 0.9 since the heading-equality signal is doing the work even when content is borderline.
3. **A 5th change type `IDENTITY` / `UNCHANGED`.** Would have made the API surface explicit about every clause that was seen, paired, and confirmed unchanged. The spec lists four types and the design doc's MOVED-suppression UI affordance covers the customer-facing case. Decided against — extra ontology with no demand.
4. **Per-portal alignment-tuning overrides.** Explicitly out of scope per the unit spec; `TuningConfig` stays global in this unit. The `align()` signature takes `tuning: TuningConfig | None = None` so per-portal injection is a one-line change at the call site when needed.
5. **Nightly Hypothesis property tests for the monotonic-DP invariant.** Listed as optional in the spec; the deterministic tests already cover the no-crossing constraint, the DP-rejection branch, both directions of the LSH same-side filter, and the IE-fixture round trip. Adding nightly property tests is defensible but not load-bearing for the demo. Deferred.

## Gotchas captured

1. **`heading_text = None` is the common case for sub-clauses.** The parser only synthesises a heading from preceding bold text — `(a)`, `(b)`, `(i)`, `(ii)` lines never get one. Any alignment logic that gates on heading equality has to have a fallback or it'll mis-classify everything below subsection level. Caught this through the IE identity end-to-end test; without it, the unit would have passed local unit tests and broken catastrophically on real fixtures.
2. **MinHash jaccard estimate variance bites at the threshold edge.** A test that constructed two preambles with exact jaccard 0.71 — barely above the default 0.7 threshold — failed because the 128-permutation estimator landed at 0.68. Made the synthetic preamble longer so the exact jaccard sat at ~0.85; the estimator now safely clears. Lesson for WU2.4's regression suite: synthetic-test bodies need enough margin for the estimator variance, not just enough to pass the exact threshold.
3. **Ruff B008 forbids function-call defaults.** `tuning: TuningConfig = default_tuning_config()` fails B008. Used `tuning: TuningConfig | None = None` with a `if tuning is None: tuning = default_tuning_config()` body. Same observable behaviour, none of the call-once-at-import-time hazard the lint exists to prevent.
4. **`Clause` is a `@dataclass(frozen=True, slots=True)`, not a Pydantic model.** The spec mentioned `model_copy(update=...)` for mutating it in tests. That's the Pydantic API — `Clause` uses `dataclasses.replace(clause, ...)` instead. Both produce a new frozen instance; only the import differs. The IE-fixture mutation test recurses the tree rebuilding from leaves up via `dataclasses.replace`.
5. **Files written via the Write tool from a worktree-bound session can land in the main checkout when the original absolute path is used.** Caught early — Write was using the project-root absolute path rather than the worktree-relative one. Fixed by copying the four written files into the worktree, reverting the main checkout, and continuing from there. Worth a future check: a precommit hook that warns when working from `~/projects/.../horizons` while a worktree under `.claude/worktrees/` is also dirty.

## Next: WU2.4 — alignment regression suite

The pipeline runs end-to-end on the IE fixture. WU2.4 will exercise it across the 31-fixture corpus collected on 2026-06-04, build synthetic v2s for each, and measure precision/recall against expected event sets. That's the calibration pass for the four tuning knobs — `shingle_k`, `signature_size`, `lsh_bands`, `similarity_threshold` — and the moment to decide whether the defaults from WU2.2 hold or need per-portal overrides. The seam is already there (`align()` takes a `TuningConfig`); WU2.4 wires it up to portal slugs and produces the headline-quality numbers for the demo.
