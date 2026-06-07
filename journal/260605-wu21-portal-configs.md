# 2026-06-05 — WU2.1: per-portal parser configs

*Last revised: 2026-06-05.*
*Path: journal/260605-wu21-portal-configs.md.*

Track 2's second unit. WU2.0 shipped the clause-tree parser earlier today
with a `ParserConfig` data class that already exposed the override seam;
WU2.1 turned that seam into a real file loader, a small curated set of
per-portal YAMLs, and a paragraph-suppression mechanism that fixes the
specific failure WU2.0 left dangling (IE's enacting formula stealing
PART 1's heading).

Closes the WU2.1 acceptance criterion: "default recogniser handles
markdown-heading + the most common inline-numbering patterns;
`parser_configs/<portal_slug>.yaml` allows per-portal overrides; loaded
at startup; tested against at least 5 jurisdictions." Five
jurisdictions plus a `_default` snapshot — see the table below.

## What shipped

1. **`core/alignment/portal_config.py`** — `load_portal_config(slug) ->
   ParserConfig` and `list_portal_slugs() -> list[str]`. Bundled YAMLs
   load via `importlib.resources.files(...)`, parse via `yaml.safe_load`,
   and validate through `ParserConfig.model_validate`. Unknown slug raises
   `KeyError`; no path computation off `__file__`, so the wheel-installed
   case Just Works.

2. **`core/alignment/parser_configs/` — six bundled YAMLs.** Real
   `__init__.py` makes the directory a regular Python subpackage so
   hatchling ships the YAMLs by default — no `force-include` needed
   (and the one I added briefly caused a double-include build error;
   removed once observed).

   | Slug | Override surface | Why |
   |---|---|---|
   | `_default` | nothing (snapshot of `default_patterns()`) | drift sentinel + worked example for new portals |
   | `ie` | adds `ignore_patterns` for the Oireachtas enacting formula | the formula otherwise becomes PART 1's heading |
   | `cz` | trims `patterns` to ČÁST / Čl. / section / subsection | Latin `(a)` / Roman `(i)` aren't part of Czech convention |
   | `au` | replaces `section` regex with `\d+(?=\s{2,}[A-Z])` | AU Determinations use `1  Name` — no period |
   | `at` | adds `§\s*\d+[a-z]?` at depth 3 (boundary required) | Austrian RIS references its own laws by `§` |
   | `eu` | empty `patterns`; `treat_unmatched_bold_as_heading: false` | EU news / press releases use markdown headings only |

3. **`ParserConfig.ignore_patterns` + `IgnorePattern`** — a paragraph
   whose stripped plain text `re.fullmatch`-es any rule is dropped
   before reaching `_find_matches`. Fullmatch (not prefix) by design:
   an under-specified ignore regex cannot accidentally elide real
   prose. This is what kills IE's enacting formula and what unblocks
   any future "boilerplate intrusion" complaint.

4. **`parse(markdown_text, *, config=None, portal_slug=None)`** — string
   slug is the production convenience; explicit `config` still
   available for tests and ad-hoc use. Precedence: `config` wins when
   both are passed.

5. **`docs/5. clause-tree-parser.md`** — documented `ignore_patterns`
   semantics, the loader API surface, and the bundled portal set in a
   short reference table.

6. **`packages/horizons-core/pyproject.toml`** — pyyaml as a runtime
   dep (alongside markdown-it-py).

7. **`tests/test_portal_config.py`** — 18 tests, all green first try
   (apart from one ruff B017 + ruff format + one pyright `list[Unknown]`
   from `default_factory=list`, all fixed inline):
   - Loader: returns `ParserConfig` per bundled slug, KeyError on
     unknown, `_default` in the listing, listing is sorted.
   - Drift: `_default.yaml` round-trips against
     `default_parser_config().model_dump()` — fails loudly the moment
     the Python source diverges from the YAML snapshot.
   - Ignore patterns: dropped paragraph leaves no leaf and no pending
     heading; fullmatch (not prefix) semantics.
   - Precedence: `parse(md, portal_slug="ie")` resolves; `config=`
     wins when both are passed.
   - Per-portal landmarks against real fixtures:
     - IE: PART 1 heading no longer contains "enacted by the
       Oireachtas".
     - CZ: ČÁST PRVNÍ / Čl. I structure recognised; `letter_para` /
       `roman_subpara` are not in the loaded pattern names.
     - AU: `1`, `2`, `3`, `4` open as section clauses (no-period
       headings).
     - AT: at least one §-marker clause opens.
     - EU: empty patterns; markdown headings carry the structure
       ("BEREC" appears in a heading).

## What I considered and didn't do

1. **YAML write / save API.** Q4 from the open-questions list — punted
   to whenever an admin UI consumer arrives. Read-only is a smaller
   surface and the YAMLs are hand-edited until there's a UI to drive
   them.

2. **YAML as canonical source of truth.** Q2 — kept `default_patterns()`
   canonical in Python and made `_default.yaml` an asserted-equal
   snapshot. Drift fails the test the next time someone touches
   `default_patterns()` and forgets the YAML, which is louder than
   one-source-of-truth would have been.

3. **Single-character Roman misclassification.** WU2.0 logged this as
   accepted-as-rare. Per-portal config can't fix it without
   document-context tracking; WU2.2's similarity stack will likely
   surface clauses where this matters and we can revisit if it bites.

4. **"Preliminary and General" still lost as PART 1's title for IE.**
   With ignore_patterns suppressing the enacting formula, PART 1's
   heading becomes the Act's long title ("PROTECTION OF EMPLOYEES…")
   instead of the enacting formula — a strict improvement, but
   "Preliminary and General" is still parked behind the long title in
   `_pending_heading` and overwritten before the next clause opens.
   Fixing that would need either (a) a heuristic that prefers shorter
   bold paragraphs as part titles, or (b) per-portal "skip-this-bold"
   in addition to ignore_patterns. Not worth doing speculatively.

## Cadence note

Worktree → ff-merge → push, same as WU2.0. Local sweep at the end was:

- `uv run pytest` — **157 passed, 1 deselected** (was 139 + 1
  deselected before, +18 new portal-config tests).
- `uv run ruff check .` + `uv run ruff format --check .` — clean.
- `uv run pyright` — 0 errors, 12 warnings (all pre-existing
  `reportMissingTypeStubs` from `testcontainers.postgres`).
- `uv run pre-commit run --all-files` — passed.
- `cd packages/horizons-core && uv build` — wheel includes all six
  `parser_configs/*.yaml` files at the expected install paths.

Webapp sweep skipped — no webapp file in the diff and the worktree
had no `node_modules`; CI on the feature-branch push runs the webapp
build anyway.

## Next session

WU2.2 — similarity stack (`core/alignment/similarity.py`): shingling
+ MinHash + LSH via `datasketch`, with a `TuningConfig` for `k`,
signature size, LSH bands, and similarity threshold. No dependency on
WU2.1 — strictly bottom-up from WU0.3. After that, WU2.3 (the
alignment pipeline) can finally take two `Clause` trees and emit
`ChangeEvent`s.
