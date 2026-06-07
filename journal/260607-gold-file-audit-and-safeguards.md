# Gold-file audit and reliability safeguards

**Date:** 2026-06-07
**Scope:** verification of the gold authored earlier today + new
safeguards against silent quality slides.

## 1. Why

After landing the synthetic_v2 gold suite
(`journal/260607-alignment-gold-suite.md`) and then expanding it for
c95911f's consistency fixes
(`journal/260607-gold-file-regenerated-for-stale-ref-fixes.md`), I
named seven specific weaknesses in how the gold had been authored:
circular authoring (paths copied from `align()` output), unverified
mapping of new entries to actual c95911f line changes, brittle path
strings, inconsistent "intent vs. emission" rule application, no
aggregate-level floor, and substrate coverage gaps. This session
worked through five of those (the substrate gap is a separate WU).

## 2. What landed

### 2.1 Verification of every c95911f-derived gold entry (step 1)

For each of the 5 affected fixtures I read the actual
`git show c95911f -- data/samples/synthetic_v2/<slug>-v2.md` diff and
mapped every gold entry to a real changed line. Findings:

- **`gb-28914588`** (2 new entries) — both correct.
- **`fr-31702142`** (1 new entry) — correct.
- **`de-20951816`** (1 new entry) — **wrong**. The change at path
  `['#2']` is not a c95911f fix; it's the line-4 summary block edit
  that the original WU8.0 v2 already carried. The aligner emits it
  as `REMOVED + ADDED`. I'd added a `MODIFIED ['#2']` entry that
  matched neither edit's intent. **Removed.**
- **`it-26863`** (6 new entries) — 5 correct + 1 mis-framed. The
  `['public-finance-…','#2'→'#1']` entry was labelled "consistency
  fix" but is actually a WU8.0 cascade-shifted edit (a real text
  change in v1→v2 the original gold didn't capture). **Kept**, with
  the comment corrected.
- **`eu-31366184`** (1 new entry) — correct.

Net effect: gold went 33 → 32 entries; aggregate F1 lifted 0.77 → 0.78
(recall 0.90 → 0.93, precision unchanged at 0.70). The spurious de
entry was always a recall miss; removing it is an honest improvement.

### 2.2 Intent-vs-emission rule explicit in YAML header (step 2)

Added an "Authoring rule" block to
`data/samples/synthetic_v2/expected_events.yaml`:

> `change_type` encodes EDITORIAL INTENT. … `paths` encode PARSER
> OUTPUT.

Means: for a small-text edit the editor wants surfaced as MODIFIED,
encode `change_type: MODIFIED` even if the aligner emits `REMOVED +
ADDED` — the recall miss is informative. For a paragraph that shifted
position due to an upstream removal, encode the actual `before_path`
→ `after_path` the parser produces, because the parser determines
paths from markdown structure, not from intent.

With this rule documented, all 32 entries are consistent — the three
`MODIFIED` entries that the aligner currently fails to detect
(`gb ['#34']`, `eu ['berec-…','#3']`, plus the implicit ones in
`de ['arbeitslosigkeit-…','#1']` after c95911f) are intent-encoded,
and the path-shifted entries (`it ['public-finance-…','#2'→'#1']`)
are emission-encoded.

### 2.3 Circularity sanity check (step 3)

`SyntheticV2Score.circularity_smell` triggers when:

- `expected_count >= 4` (small fixtures with 2–3 entries can
  legitimately match exactly without it being suspicious), AND
- `true_positives == expected_count` (every gold entry matched), AND
- `true_positives == actual_count` (no FPs)

When set, the terminal-summary table appends `[circularity smell]` to
the notes column and prints a separate one-line warning naming the
flagged fixtures with the recommended remediation (independently
re-author from the v2 markdown). Heuristic, not assertion — current
run flags `fr-31702142`, which step 4 confirms is genuinely correct.

### 2.4 Independent re-authoring of three fixtures (step 4)

Picked `au-2145602` (small/simple), `fr-31702142` (smell-flagged),
`gb-28914588` (large/complex with misses + extras). Read v1 and v2
markdown directly, identified editorial edits by eye, then diffed
against the existing gold entries.

All three verify as correct:

- **`au-2145602`**: 2 entries (1 REMOVED + 1 ADDED). Sub-paragraph
  `(iv)` removal + Schedule 1 closing sentence added. Paths match
  what the parser produces.
- **`fr-31702142`**: 4 entries. 1 REMOVED (para 43) + 1 MODIFIED
  (para 44, both 20→25 M€ and "points 40 à 43" → "points 40 à 42"
  in the same clause) + 1 ADDED (para 45) + 1 MODIFIED (ARTICLE 1ER
  dispositif). All map to real WU8.0 and c95911f edits. The
  circularity smell is a TRUE smell (paths were copied from
  `align()`) but the underlying gold is correct in this case —
  smell is a "consider investigating" flag, not a verdict.
- **`gb-28914588`**: 5 entries. The `'#34'` / `'#35'` / `'#40'`
  positional paths correspond to paragraph 9 / 9A / 13 because the
  parser splits paragraph 10's `a.` / `b.` sub-items into separate
  unnumbered-paragraph leaves, expanding the index gap. The c95911f
  entries at `['210.', '#10']` and `['210.', '#21']` correctly map
  to paragraphs 29 and 34 under the "210." heading.

### 2.5 Aggregate-F1 floor assertion (step 5)

`test_zz_aggregate_f1_above_floor` (named `zz` to sort after the
parametrized per-fixture tests) asserts that the macro-F1 across all
8 fixtures clears `AGGREGATE_F1_FLOOR = 0.65`. Current baseline is
0.78, leaving a 0.13-point cushion for per-fixture noise. The test
skips (rather than fails) when `-k`-filtered to a subset, since the
aggregate only makes sense over the full suite.

Sanity-checked by temporarily raising the floor to 0.90 and
confirming the assertion fires; reverted.

## 3. New numbers

```
fixture      N    TP   P     R     F1    notes
au-2145602   2    2    1.00  1.00  1.00
de-20951816  3    3    0.60  1.00  0.75  2 extra event(s)
eu-31366184  3    2    0.50  0.67  0.57  missed MODIFIED ['berec-…','#3'], 2 extra event(s)
fr-31702142  4    4    1.00  1.00  1.00  [circularity smell]
gb-28914588  5    4    0.36  0.80  0.50  missed MODIFIED ['#34'], 7 extra event(s)
ie-27732019  2    2    1.00  1.00  1.00
ie-8064194   4    4    0.44  1.00  0.62  5 extra event(s)
it-26863     9    9    0.69  1.00  0.82  4 extra event(s)
aggregate    32   30   0.70  0.93  0.78
```

Per-fixture changes from yesterday's regenerated gold:

- `de-20951816`: 4/3 → 3/3 (spurious entry removed; R 0.75 → 1.00).
- All other fixtures unchanged.
- Aggregate: 33/30 → 32/30; F1 0.77 → 0.78.

## 4. What's still NOT done

Two of the seven weaknesses I named remain open:

- **Path-string brittleness** — gold hard-codes parser-derived paths
  like `arbeitslosigkeit-unterbeschäftigung-und-erwerbslosigkeit`
  (note: docstring claims diacritic-stripping but the parser
  preserves them — either the docstring is wrong or the parser is).
  Any change to slugifier or numbering would silently break the test.
  Mitigations: (a) pin the parser version in the gold header and
  fail loudly on mismatch, (b) re-derive paths at test load time
  from heading text + paragraph index, (c) accept brittleness and
  rely on the smell-check + aggregate-F1 floor to catch shifts.
  None of these is free; deferred until first parser change forces
  the issue.
- **Substrate coverage gaps** — no fixture exercises MOVED across
  heading boundaries, wholesale section restructuring, or nesting
  beyond 4 levels. Not a reliability issue with the existing gold,
  but limits what the suite can prove. Expanding the corpus is its
  own WU.

## 5. Reliability verdict

The gold is now **substantively reliable**: every c95911f-derived
entry is verified against the actual diff, three fixtures verified by
independent re-authoring, the intent-vs-emission rule is explicit, a
circularity smell-check flags the one fixture where paths were copied
from `align()` (and that fixture is independently confirmed correct),
and the aggregate F1 floor catches regression slides.

Remaining caveats: the path strings are still brittle to parser
changes (no version pin), and the suite's substrate doesn't exercise
some failure modes the aligner could plausibly hit. Both are flagged
above as separate work.
