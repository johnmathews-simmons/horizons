# Gold file regenerated for c95911f stale-reference fixes

*Last revised: 2026-06-07.*
*Path: journal/260607-gold-file-regenerated-for-stale-ref-fixes.md.*

**Date:** 2026-06-07
**Scope:** gold file maintenance, not aligner tuning.

## 1. Context

Earlier today (`journal/260607-alignment-gold-suite.md`) I committed
the synthetic_v2 gold-file calibration suite with 22 expected events
across 8 fixtures. A parallel session subsequently landed `c95911f`
("close stale references in five synthetic v2 fixtures") which
applied 11 hygiene edits across the v2 files — consistency fixes for
text that had become internally contradictory after the primary
WU8.0 edits (e.g. paragraph 29 still cited the old 2% ACAS uplift
after paragraph 9 was modified to 5%).

Those edits are real clause-level diffs the aligner will detect. The
22-entry gold did not account for them, so they all showed up as
extras (FPs) and suppressed precision below what the aligner actually
deserved.

## 2. What changed

`data/samples/synthetic_v2/expected_events.yaml`: 22 → 33 entries.

| Fixture | Before | After | Added |
|---|---|---|---|
| `gb-28914588` | 3 | 5 | 2 MODIFIEDs (paras 29 + 34) |
| `fr-31702142` | 3 | 4 | 1 MODIFIED (ARTICLE 1ER dispositif) |
| `de-20951816` | 3 | 4 | 1 MODIFIED (opening-narrative restatement) |
| `it-26863` | 3 | 9 | 6 MODIFIEDs (3 tables + 2 narratives + 1 cascade-shifted sibling) |
| `eu-31366184` | 2 | 3 | 1 MODIFIED ("hybrid format" rewrite) |

`data/samples/synthetic_v2/README.md`: each affected fixture's
section gained a **Consistency fixes (c95911f)** bullet documenting
the editorial intent of the hygiene edits.

## 3. New baseline

Same `align()`, same default `TuningConfig`:

| Suite | F1 before c95911f gold update | F1 after |
|---|---|---|
| Synthetic-mutation regression | 0.97 | 0.97 (corpus grew via WU8.7, no algorithmic change) |
| Gold-file calibration | 0.69 | **0.77** |

Per-fixture breakdown:

```
fixture      N    TP   P     R     F1    notes
au-2145602   2    2    1.00  1.00  1.00
de-20951816  4    3    0.60  0.75  0.67  missed MODIFIED ['#2'], 2 extra event(s)
eu-31366184  3    2    0.50  0.67  0.57  missed MODIFIED ['berec-…','#3'], 2 extra event(s)
fr-31702142  4    4    1.00  1.00  1.00
gb-28914588  5    4    0.36  0.80  0.50  missed MODIFIED ['#34'], 7 extra event(s)
ie-27732019  2    2    1.00  1.00  1.00
ie-8064194   4    4    0.44  1.00  0.62  5 extra event(s)
it-26863     9    9    0.69  1.00  0.82  4 extra event(s)
aggregate    33   30   0.70  0.90  0.77
```

Direction-of-travel:

- **fr-31702142, gb-28914588, it-26863** improved (it the most: 0.38 → 0.82). The aligner already emits MODIFIED at the right anchors for these consistency-fix paragraphs; the original gold just didn't count them.
- **de-20951816, eu-31366184** slightly degraded (each lost one R point). Both are small-text-edits in short paragraphs that the aligner emits as `REMOVED + ADDED` instead of `MODIFIED` — same failure mode that already gates `gb-28914588`'s paragraph 9. Encoding them as MODIFIED (editorial intent) rather than REMOVED+ADDED (aligner behaviour) keeps the gold honest at the cost of one recall point each.
- **au-2145602, ie-27732019, ie-8064194** unchanged — no consistency fixes touched them.

## 4. Three small-text-edit misses

The gold now surfaces three instances of the same failure pattern:

| Fixture | Path | Edit |
|---|---|---|
| `gb-28914588` | `['#34']` | "2% → 5%" ACAS uplift |
| `de-20951816` | `['#2']` | "3.070.000 → 3.105.000" total |
| `eu-31366184` | `['berec-…','#3']` | "hybrid format" → in-person only |

All three are short paragraphs (~80–160 chars) where the aligner
fails to pair v1 and v2 leaves and emits `REMOVED + ADDED` instead.
Two candidate root causes (untested, same as the earlier journal
note for `gb-28914588`):

- Pass 2's `_pass2_confidence` short-body fallback requires
  byte-equal `body_text` — a single-char numeric edit fails that.
  Pass 2's shingle path requires `has_shingles` on both sides, which
  needs body length ≥ `shingle_k` words (default 7) — borderline for
  these paragraphs.
- Greedy descending-confidence sort in `_pass_heading_match`
  consumes a near-duplicate neighbour before the right pair.

Fix candidates worth trying post-demo:

1. **Tighten the pass-2 short-body rule** to accept high jaccard on
   character-shingles (instead of byte-equal text) for bodies below
   the word-shingle threshold. Single-char numeric edits would then
   stay as MODIFIED.
2. **Pass-4 post-processing** that re-pairs a (REMOVED, ADDED) pair
   at the same path into a MODIFIED event when both sides have text
   that's near-equal under character-jaccard. Cheaper than re-tuning
   thresholds; keeps the pass-2/3 paths unchanged.

Both belong in a post-demo tuning WU. The gold's job here is to keep
the failure mode visible across the demo period.

## 5. Cross-references updated

- `docs/runbooks/alignment-calibration.md` — headline number updated
  to 0.77, known-issues section expanded to list all three small-edit
  misses with the same pattern.
- `docs/RFC-2 clause-alignment.md` — *Calibration* section baseline
  number updated to 0.77.
- `journal/260607-alignment-gold-suite.md` — left untouched (it's the
  point-in-time record of the original suite landing); this file is
  the follow-up.
