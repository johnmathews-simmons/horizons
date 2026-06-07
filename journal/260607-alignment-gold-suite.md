# Alignment gold-suite on the synthetic_v2 corpus

**Date:** 2026-06-07
**Scope:** evaluation, not aligner tuning.

## 1. Why

`tests/alignment/test_fixtures.py` reports macro-F1 = 0.97 on the
27-fixture synthetic-mutation suite (one ADDED + one REMOVED + one
MODIFIED + one MOVED per fixture, leaves picked at random by a
per-slug RNG). That number is true, but it only proves the aligner
handles **isolated leaf edits** — the mutation synthesiser never
inserts or removes a clause that pushes its siblings down, so the
suite never exercises the cascading-renumber failure mode that real
amendments routinely trigger.

The eight hand-authored v1↔v2 pairs in `data/samples/synthetic_v2/`
were already on disk (WU8.0, WU8.6) but had no calibration tooling.
This session added one.

## 2. What landed

- `data/samples/synthetic_v2/expected_events.yaml` — gold encoding of
  the editorial change events documented in `README.md`. 22 expected
  events across 8 fixtures (six languages: EN, EN-IE, FR, DE, IT,
  GA-flavoured-EN), all four change types represented.
- `tests/alignment/test_synthetic_v2.py` — parametrised pytest module;
  per-fixture floor is `TP >= max(1, 0.5 * N_expected)`; full P/R/F1
  reported via the existing terminal-summary hook.
- `tests/alignment/conftest.py` — extended with `SyntheticV2Score` +
  `record_synthetic_v2()` + a second rendered table.

## 3. Numbers

Same `align()` call, same default `TuningConfig`, two different test
substrates:

| Suite | Fixtures | Macro-P | Macro-R | Macro-F1 |
|---|---|---|---|---|
| `test_fixtures` (random synthetic mutations) | 27 | 0.96 | 0.97 | 0.97 |
| `test_synthetic_v2` (hand-authored realistic v2) | 8 | 0.59 | 0.96 | 0.69 |

Recall is the same story on both substrates (~0.96): when an edit
exists, the aligner usually finds something at the right anchor. The
collapse is in **precision** — the gold suite has fixtures where the
aligner emits 11 events for 3 editorial edits, because removing or
inserting a paragraph in the middle of a flat sibling list cascades
into N-1 spurious MOVED events on each unchanged paragraph below the
edit point.

Per-fixture breakdown:

```
fixture      N    TP   P      R      F1     notes
au-2145602   2    2    1.00   1.00   1.00
de-20951816  3    3    0.60   1.00   0.75   2 extra event(s)
eu-31366184  2    2    0.50   1.00   0.67   2 extra event(s)
fr-31702142  3    3    0.75   1.00   0.86   1 extra event(s)
gb-28914588  3    2    0.18   0.67   0.29   missed MODIFIED ['#34'], 9 extra event(s)
ie-27732019  2    2    1.00   1.00   1.00
ie-8064194   4    4    0.44   1.00   0.62   5 extra event(s)
it-26863     3    3    0.23   1.00   0.38   10 extra event(s)
```

Two fixtures (au-2145602, ie-27732019) score 1.0 across the board.
ie-27732019 is the demo's headline MOVED beat (section 11 → 11A); good
to confirm it scores cleanly on both axes.

## 4. The one recall miss

`gb-28914588`'s paragraph 9 ("ACAS uplift 2% → 5%") is the only
recall miss in the suite. Aligner emitted it as `REMOVED ['#34']` +
`ADDED ['#34']` instead of `MODIFIED ['#34']`. Both v1 and v2 leaves
have `heading_text=None` and identical path `('#34',)`; pass 2 should
pair them on the path-equality rule before pass 3 even runs. It
doesn't. Two candidate causes (untested):

- Pass 2 confidence dropped below `similarity_threshold` because the
  body is short and the shingled jaccard estimate is noisy.
- The `_pass_heading_match` greedy sort consumed one side against a
  near-duplicate before the right pair was considered.

Not investigated further this session — fix belongs in a tuning WU.
The gold suite's job here is to surface it as a number; consider it
surfaced.

## 5. The precision story

Three fixtures dominate the precision drop, and the failure mode is
the same in all three: **flat-sibling renumbering after an insert /
remove**. The aligner's monotonic DP in `_pass_content_monotonic`
correctly pairs each `('#N',)` leaf with `('#N±1',)` after the
shift, then emits each as a MOVED event because the path changed.
From the user's perspective these aren't separate changes; they're
the *consequence* of one edit.

Two ways to suppress this downstream — out of scope for this session,
but worth flagging for the post-demo tuning pass:

1. **Aligner-side:** if the path delta on a MOVED matches a global
   shift induced by an adjacent ADDED/REMOVED, collapse it into the
   inducing event. Cheaper than re-tuning thresholds.
2. **Repository/UI side:** treat MOVED-with-identical-text-and-
   contiguous-shift as a derived class the UI filters out by default.
   Keeps the aligner output complete (some customers may want the
   structural shift visible) and lets product surface the editorial
   set.

## 6. What this changes about the test story going forward

Two complementary calibration numbers now:

- `test_fixtures` (broad, random) — guards against catastrophic
  regression across 27 corpora. Macro-F1 ≥ 0.95 is the implicit gate.
- `test_synthetic_v2` (small, naturalistic) — guards against the
  realistic-amendment failure modes. Macro-F1 of 0.69 is the current
  baseline; any tuning work should report both numbers, and any drop
  in either should be explained.

The gold YAML is the load-bearing artefact — extend it as the
synthetic_v2 corpus grows. New v2 pairs added to `synthetic_v2/`
without a gold entry will simply not be tested; that's fine, the test
parametrises over the gold not the directory.
