# Alignment calibration

How to read, run, and extend the two alignment-quality suites that
score `horizons_core.core.alignment.align`. Background and design
choices live in `docs/RFC-2 clause-alignment.md` — *Calibration*; this
runbook is the day-to-day operating manual.

## 1. Metrics

Both suites print **precision**, **recall**, and **F1** per fixture
plus a macro-averaged aggregate row. With:

- **TP** (true positive) — aligner emitted an event matching a gold entry on `change_type` and paths.
- **FP** (false positive) — aligner emitted an event with no matching gold entry (e.g. a cascading paragraph renumber).
- **FN** (false negative) — gold entry the aligner did not emit a matching event for.
- **N** (column in the gold-suite table) — expected event count for that fixture, i.e. how many entries the gold YAML lists. `TP / N` is recall written as a fraction with its denominator visible.

The scores derive from those:

- **Precision** = TP / (TP + FP) — of what the aligner emitted, what fraction were real edits.
- **Recall** = TP / (TP + FN) = TP / N — of the real edits, what fraction did the aligner catch.
- **F1** = 2 · P · R / (P + R) — harmonic mean of P and R; weights them equally.

**Why not F2?** F2 (and any F-beta with β > 1) weights recall above
precision — the right metric when missing a real event is more costly
than emitting a spurious one (cancer screening is the canonical
example). For regulatory-change tracking that framing is arguable
(missing a law change *is* operationally worse than a false alert),
but it doesn't change the current calibration story: recall is the
stronger axis on both suites (~0.90 on the gold suite, ~0.97 on the
mutation suite) while precision is the bottleneck (~0.70 on the gold
suite). Switching to F2 would print a flattering higher number
without changing which knob to tune next. If precision ever rises
past recall, F2 becomes the more honest single-number report.

## 2. The two suites

| Suite | File | Substrate | Size | Headline number |
|---|---|---|---|---|
| Synthetic-mutation regression | `tests/alignment/test_fixtures.py` | every `*.md` in `data/samples/` mutated with one ADDED + one REMOVED + one MODIFIED + one MOVED (deterministic per slug) | 27 fixtures × 2 cases | macro-F1 ≈ 0.97 |
| Gold-file calibration | `tests/alignment/test_synthetic_v2.py` | the 8 hand-authored v1↔v2 pairs in `data/samples/synthetic_v2/`, scored against `data/samples/synthetic_v2/expected_events.yaml` | 8 fixtures, 32 expected events | macro-F1 ≈ 0.78 |

They answer different questions. The mutation suite asks *"did the
aligner break on random leaf edits across a wide corpus"* — catches
catastrophic regressions. The gold suite asks *"does the aligner
recover the editorial intent of a realistic legal amendment"* —
catches precision-collapsing cascades (insert one paragraph, watch
five unchanged siblings get emitted as MOVED events) that random
leaf mutations cannot trigger by construction.

Same `align()` call, same default `TuningConfig` — the two numbers
diverging is the signal. Report both whenever you change tuning.

## 3. Commands

### 3.1 Gold suite only (fast)

```bash
uv run pytest tests/alignment/test_synthetic_v2.py --no-cov
```

~3 seconds. Use this when you've tweaked `TuningConfig` defaults, the
parser, or one of the alignment passes and want a quick "did I move
the realistic-amendment number". Prints one table.

### 3.2 Both suites (~45 s)

```bash
uv run pytest tests/alignment/ --no-cov
```

Prints both tables back-to-back — the 27-fixture synthetic-mutation
table first, then the 8-fixture gold table. This is what you want
before declaring a tuning change a win; a change that lifts one
number and tanks the other is a sideways move, not progress.

### 3.3 One fixture, with full notes

```bash
uv run pytest tests/alignment/test_synthetic_v2.py -k <slug> --no-cov
```

(e.g. `-k gb-28914588`.) Single-fixture run; the assertion message
lists every miss and extra-event count with paths quoted, so you can
grep the aligner's emitted events against the gold.

## 4. Before/after baseline workflow

The right way to measure a tuning change. Capture, change, recapture,
diff:

```bash
# Before
uv run pytest tests/alignment/ --no-cov 2>&1 \
  | grep -A 100 'quality report\|synthetic_v2 gold' > /tmp/align-before.txt

# ... edit horizons_core/core/alignment/ ...

# After
uv run pytest tests/alignment/ --no-cov 2>&1 \
  | grep -A 100 'quality report\|synthetic_v2 gold' > /tmp/align-after.txt

diff /tmp/align-before.txt /tmp/align-after.txt
```

The aggregate rows give the headline number; the per-fixture rows
tell you whether the change helped uniformly or robbed Peter to pay
Paul.

## 5. Reading the tables

### 5.1 Mutation suite (`test_fixtures`)

```
fixture            ident   P     R     F1    notes
ie-27732019-v1     ok      1.00  1.00  1.00
at-32061749-v1     ok      0.60  0.75  0.67  missed MODIFIED, 2 extra event(s)
aggregate          31/31   0.96  0.97  0.97  4 skipped (fixture too small)
```

- `ident` — `ok` if `align(v1, v1) == []`. A `FAIL (N)` here means
  re-ingesting an unchanged version would emit `N` spurious diffs to
  customers; **this is the load-bearing correctness check** and it
  fails the build (the other columns are advisory).
- `P / R / F1` — against the four synthesised events. `R` is `TP/4`;
  `P` is `TP/len(emitted)`.
- Skipped rows (fixtures under ~5 KB) still run the identity case;
  they only skip the mutation case because there isn't enough body to
  synthesise four distinct mutations.

### 5.2 Gold suite (`test_synthetic_v2`)

```
fixture      N   TP   P     R     F1    notes
au-2145602   2   2    1.00  1.00  1.00
fr-31702142  4   4    1.00  1.00  1.00  [circularity smell]
gb-28914588  5   4    0.36  0.80  0.50  missed MODIFIED ['#34'], 7 extra event(s)
aggregate    32  30   0.70  0.93  0.78
```

- `N` — expected event count for this fixture (from
  `expected_events.yaml`).
- `TP` — matched gold entries.
- Extras (`7 extra event(s)`) are typically cascading paragraph
  renumbers triggered by one insert/remove — legitimate from the
  aligner's POV, noise from the customer's.
- `[circularity smell]` — `N ≥ 4`, no FPs, no FNs. Heuristic flag
  meaning "the gold matches aligner output suspiciously exactly,
  likely because it was authored by copying `align()` rather than
  reading the markdown independently." A true smell is not a failure
  — investigate the fixture; if the aligner is genuinely correct on
  it, leave the entry alone.
- The aggregate `N` and `TP` columns are *totals* (32 events across
  8 fixtures, 30 matched). `P / R / F1` aggregate columns are
  *macro-averages* across the per-fixture rows.

## 6. Extending the gold

Drop a new pair into `data/samples/synthetic_v2/`:

1. Author `<slug>-v1.md` (or use one that's already in
   `data/samples/`) and a `<slug>-v2.md` with your editorial edits.
2. Add a `<slug>:` block to `data/samples/synthetic_v2/expected_events.yaml`
   listing the expected `(change_type, before_path, after_path)`
   tuples. ADDED and REMOVED entries omit the path they don't carry.
3. Run command 3.1 to verify.

To discover the path strings the parser produces for your new fixture
without reading them off the markdown by eye:

```bash
uv run python - <<'PY'
from pathlib import Path
from horizons_core.core.alignment.parser import parse
from horizons_core.core.alignment.align import align
from horizons_core.core.alignment.portal_config import load_portal_config

slug = "xx-12345"  # your slug
v1 = Path("data/samples") / f"{slug}-v1.md"
v2 = Path("data/samples/synthetic_v2") / f"{slug}-v2.md"

def _parse(path):
    iso = path.stem.split("-", 1)[0]
    text = path.read_text(encoding="utf-8")
    try:
        return parse(text, config=load_portal_config(iso))
    except KeyError:
        return parse(text)

for e in align(_parse(v1), _parse(v2)):
    print(e.change_type, list(e.before_path) if e.before_path else "—",
          "->", list(e.after_path) if e.after_path else "—")
PY
```

Copy the paths for the events you want to assert as editorial intent;
extras the aligner emitted but you don't list will (correctly) lower
precision. The test auto-discovers from the gold file, not from disk
— fixtures without a gold entry aren't tested.

## 7. Floors and gates — what is and isn't enforced

| Check | Where | Enforcement |
|---|---|---|
| `align(v1, v1) == []` (identity case) | `test_fixtures.py::test_identity_emits_no_events` | **Hard assertion** per fixture; fails the build. |
| Mutation case `TP >= 2` (out of 4) | `test_fixtures.py::test_four_mutations_align_correctly` | Hard assertion per fixture; catastrophe floor only. |
| Gold case `TP >= max(1, ⌈N/2⌉)` | `test_synthetic_v2.py::test_synthetic_v2_alignment` | Hard assertion per fixture; catastrophe floor only. |
| Gold aggregate macro-F1 ≥ `AGGREGATE_F1_FLOOR` (currently 0.65) | `test_synthetic_v2.py::test_zz_aggregate_f1_above_floor` | **Hard assertion** on the full-suite run; skipped if `-k`-filtered to a subset. |
| Mutation suite aggregate P/R/F1 | terminal-summary table | **Not gated.** Calibration diagnostic; read with your eyes. |

Both suites run inside the routine `uv run pytest` sweep — you don't
need to invoke them separately before pushing main. The aggregate-F1
floor (`AGGREGATE_F1_FLOOR = 0.65`) catches a real algorithmic
regression that pushes several fixtures' precision down at once; a
single-fixture wobble inside the 0.13-point cushion won't fire it.

To tighten the gate after a tuning win, raise `AGGREGATE_F1_FLOOR` in
`test_synthetic_v2.py` to ~0.05 below the new baseline. To tighten
per-fixture, raise `TP_FLOOR_RATIO` (currently `0.5`).

## 8. Known issues surfaced by the gold suite

As of 2026-06-07 (see `journal/260607-alignment-gold-suite.md` and
`journal/260607-gold-file-regenerated-for-stale-ref-fixes.md` for
context):

- **Cascading renumber events** — `gb-28914588`, `it-26863`, and
  `ie-8064194` over-emit because removing/inserting one paragraph
  pushes its siblings down and the aligner emits each as MOVED. Out
  of scope to fix pre-demo; two mitigations sketched in the journal
  entry (aligner-side coalescing, or UI-side filtering of
  identical-text contiguous-shift MOVEDs).
- **Small-text edits emitted as REMOVED + ADDED** — three fixtures
  hit the same pattern: `gb-28914588 ['#34']` ("2% → 5%" ACAS uplift),
  `de-20951816 ['#2']` ("3.070.000 → 3.105.000" total), and
  `eu-31366184 ['berec-…','#3']` ("hybrid format" → in-person only).
  All are short paragraphs (~80–160 chars) where the aligner fails
  to pair v1 and v2 leaves and emits `REMOVED + ADDED` instead of
  `MODIFIED`. Two candidate root causes (untested): pass 2's
  short-body fallback requires byte-equal `body_text` (single-char
  numeric edits fail it) and the shingle path needs body length ≥
  `shingle_k` words; or the greedy descending-confidence sort in
  `_pass_heading_match` consumes a near-duplicate neighbour first.
  Fix candidates also in the journal entry.

Neither blocks demo readiness; both are the current baseline, and
fixing either is post-demo tuning work.
