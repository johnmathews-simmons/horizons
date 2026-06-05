# 2026-06-05 — WU2.0: clause-tree parser

Opens Track 2 — the alignment pipeline. WU2.0 is the *bottom* of that
stack: a pure markdown → immutable clause-tree transform. No DB, no I/O,
no `clause_uid` assignment (that's WU2.3 once similarity lands in
WU2.2). The two-axis isolation spine (Track 1) finished yesterday; this
unit has no dependency on it.

## What shipped

1. **`core/alignment/clause.py`** — frozen / slots `Clause` dataclass
   with `path: tuple[str, ...]`, `heading_text`, `body_text`,
   `numbering_label`, and recursive `children: tuple[Clause, ...]`.
   Tuples (not lists) so the tree is hashable and safe to share across
   alignment stages. `walk()` yields a DFS pre-order traversal.

2. **`core/alignment/config.py`** — Pydantic v2 `ParserConfig` +
   `StructuralPattern`. Default patterns cover the IE and CZ sample
   fixtures:

   | Name | Depth | Boundary required? |
   |---|---:|---|
   | `ie_part` (`PART N`) | 1 | no |
   | `cz_cast` (`ČÁST <ord>`) | 1 | no |
   | `cz_clanek` (`Čl. I`) | 2 | no |
   | `section` (`N.`, `4A.`) | 4 | yes |
   | `subsection` (`(N)`) | 5 | yes |
   | `roman_subpara` (`(i)`, `(ii)`, …) | 7 | yes |
   | `letter_para` (`(a)`) | 6 | yes |

   Keyword patterns (PART / ČÁST / Čl.) match anywhere; paren and
   bare-number patterns require start-of-paragraph or a preceding
   sentence terminator + whitespace. This is what keeps `(1)` recognised
   as a structural anchor at paragraph start but ignored when it
   appears inside a citation like *"in subsection (1)"*.

3. **`core/alignment/parser.py`** — `parse(markdown_text, *, config)`.
   markdown-it-py token stream → two composed recognisers sharing one
   pattern list:
   - **Heading-anchored** (IE substrate): each paragraph contributes a
     handful of paragraph-leading markers.
   - **Inline-numbered** (CZ substrate): markers flow mid-paragraph in
     long prose runs; the boundary check separates structural anchors
     from citations.

   Tree-building uses a stack keyed on depth. Same-depth markers pop
   and push siblings; deeper markers nest. Depth gaps are allowed (CZ
   jumps from `Čl. I` at depth 2 to `1.` at depth 4 without anything
   at 3).

   Bold-only paragraphs with no structural marker become *pending
   headings* — they attach to the next-opened clause as
   `heading_text`, which is how the IE bold section-titles
   ("Short title, citation and commencement") get into the tree
   immediately before the bold-wrapped section number. Unrecognised
   prose becomes its own leaf with `numbering_label=None` (Q4 design
   choice) so a downstream content change to a stray paragraph is a
   discrete change event rather than silently merging into the
   neighbouring section's body.

4. **`docs/5. clause-tree-parser.md`** — design doc covering the
   pattern model, recogniser composition, pending-heading mechanism,
   and the configuration seam for WU2.1's per-portal overrides.

5. **Tests** in `packages/horizons-core/tests/test_parser.py` — IE and
   CZ fixtures, a synthetic 5-deep ladder (`PART 1 / 1. / (1) / (a) /
   (i)`), markdown `#`-heading handling, pending-heading attachment,
   `treat_unmatched_bold_as_heading=False` toggle, custom pattern
   override, plus direct unit coverage on `_has_boundary_before`,
   `_is_bold_only`, and `_slugify`. **100 % line + branch coverage** on
   the new module.

## Design decisions resolved up front

The four Q's John pre-answered before any edit:

1. **Tests live in `packages/horizons-core/tests/`** — per-package,
   next to the parser. Parser has no cross-package coupling so
   cross-package `tests/` was wrong.
2. **Parser lives in `horizons-core`**, not `horizons-ingestion`.
   `horizons-ingestion` is still an empty stub; Track 3 will import
   alignment from core, so the dependency direction is set.
3. **Tree shape: nested + path on every node.** Alignment (WU2.3)
   needs structural locality; a flat `list[Clause]` with path-only
   hierarchy would throw that away.
4. **Unrecognised paragraphs → own leaf with `label=None`**, not
   concatenated into the parent's body. Atomicity matters for diff.

## Design choices made during implementation

1. **Boundary check for paren / number markers.** Initial pass had
   the parser greedily matching every `(1)` in the CZ fixture, which
   meant inline citations split clauses. Added `requires_boundary` to
   each `StructuralPattern`; checked against start-of-paragraph or
   a preceding terminator in `_TERMINATORS = '.!?;:)"“”„—'` followed
   by whitespace. Keyword patterns (`PART`, `ČÁST`, `Čl.`) skip the
   check because they're unambiguous structural anchors.

2. **Single-char Roman vs Latin letter.** `(i)`, `(c)`, `(d)`, `(l)`,
   `(m)`, `(v)`, `(x)` are all simultaneously valid Roman numerals
   and Latin letters. The pragmatic split: Roman regex is
   `\((?:i+|[ivxlcdm]{2,})\)` — matches `(i)`, `(ii)`, `(iv)`, `(xi)`
   etc., but NOT single non-`i` chars like `(c)`. With Roman listed
   before letter in default config, the precedence works out: `(i)`
   → Roman depth 7; `(a)` / `(c)` / `(d)` → letter depth 6. The known
   miss is a standalone `(v)` (Roman 5) right after `(iv)` — it would
   classify as a letter. Accepted; vanishingly rare in legal docs and
   the IE acceptance test was about `(i)` specifically.

3. **Pending-heading queue is depth-1 (most recent only).** When two
   bold-only paragraphs come back-to-back (IE part-title then
   section-title), the first attaches to the currently-open clause if
   it has no heading; the second becomes pending and attaches to the
   next-opened numbered clause. Multi-level queue felt premature.

4. **`requires_boundary` tolerates the CZ separator `"."` + space.**
   CZ documents end each numbered item with `…“. ` (closing quote +
   period + space) and start the next with `13. V § 7…`. The walk
   back through whitespace lands on the period; terminator set
   accepts it. Confirmed against the fixture without special-casing.

5. **`heading_depth_offset` config knob.** Markdown `#`-style
   headings default to `depth = heading_level`. For documents where
   `# Section` should sit *below* part-level structure, the offset
   shifts everything down. Adds the seam for portal-specific
   ingestion without baking heading semantics into the parser.

## Known limitations (carried forward)

1. **CZ fixture produces some duplicate `N.` clauses.** The CZ
   document has inline references like `…“. ` followed by a number
   that *isn't* a structural marker but passes the boundary check.
   These appear as spurious siblings under `Čl. I`. The tests don't
   assert clause counts on CZ — they assert landmark paths exist
   (`ČÁST PRVNÍ`, `Čl. I`, `1.`, `(N)`-subsections). WU2.1 (per-portal
   config) will tighten the CZ section pattern to require the full
   close-quote-period-space prefix.

2. **`(v)`, `(x)`, `(c)`, `(d)` single-char Roman misclassification.**
   See design note 2 above. Not blocking demo.

3. **Heading_text mismatch between fixture and tests for PART 1.**
   The IE document's PART 1 has an *enacting formula* paragraph ("Be
   it enacted by the Oireachtas as follows:") that lands as PART 1's
   heading rather than the actual title "Preliminary and General".
   The enacting formula appears between PART 1 and the part title in
   the source. Acceptable for now — the parser doesn't know to skip
   enacting formulas, but the substantive title-attachment for
   sections (which is the case the test actually asserts on) works.
   WU2.1 can add a per-portal ignore-pattern for the enacting
   formula.

## Cumulative state after WU2.0

- Default-marker suite: 139 tests passing (was 90 after WU1.9). 49 new
  parser tests.
- Coverage: 100% line + branch on tracked source (unchanged bar).
- markdown-it-py 4.2.0 added as horizons-core runtime dep.
- Three Python members still: core, ingestion, api. Ingestion still
  has no code beyond `__init__.py` and `py.typed`.
- Webapp untouched.

## Next session priorities

WU2.1 (per-portal config overrides) is the natural next step — the
default config already has the seam, but the CZ duplicates and IE
enacting-formula miss above are the test cases that motivate real
per-portal YAML. After that, WU2.2 (shingling / MinHash / LSH
similarity stack) and WU2.3 (alignment + ChangeEvent) close Track 2.

Track 3 (ingestion worker) is the other open workstream; it depends
on the alignment pipeline closing first.

## Cadence note

Worktree → commit → push feature → ff-merge into main → push main →
delete remote branch → `ExitWorktree(remove, discard_changes=true)`.
Local sweep is the gate: pytest, ruff check, ruff format check,
pyright, pre-commit run --all-files, plus webapp lint:check + build
+ vitest. All green before push.
