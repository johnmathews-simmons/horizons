# 2026-06-07 — Parser heading off-by-one + invisible MOVED label

## Two bugs the user found while comparing v1/v2 panes for `ie-27732019`

The synthetic v2 fixture renames clause `**11\.**` → `**11A\.**` (body
byte-identical). The alignment pipeline correctly flagged the clause as
`MOVED` (path `["PART 2","11."]` → `["PART 2","11A."]`). The user
reported it as a false positive — the rendered bodies looked identical
side-by-side. Two underlying causes, fixed independently.

## 1. Parser heading mis-attribution (off-by-one)

`_TreeBuilder._absorb_bold_heading` attached a bold-only paragraph to
whatever clause happened to be on the stack top, *as long as* that
clause's `heading_text` was still `None`. When the stack top was a
mid-document leaf or an open numbered clause with body already
accumulated, the heading got stolen. Net effect on the IE fixture:
every "Amendment of section N of Principal Act" heading landed on the
*previous* clause's subtree, so the clause that amends section 10 was
labelled "section 11", and so on.

**Fix:** added a body/children guard so the heading only attaches to a
freshly-opened structural clause (no body, no children, no heading
yet). Otherwise it defers via `_pending_heading` and binds to the next
opened structural clause, the way Part-title headings already worked.

**Tests:**

- `test_bold_heading_after_open_clause_does_not_attach_to_stack_top`
  — synthetic regression covering the (a)-leaf-steals-heading shape.
- `test_ie_section_11_heading_describes_what_it_actually_amends`
  — fixture-level guard pinning the real-world case.

## 2. `numbering_label` was never rendered

The parser emits `numbering_label` (e.g. `11.`, `11A.`) but only the
slugified form survived in `clause_path`. The continuous-mode renderer
in `ClauseOverlay.vue` showed `heading_text` and `text_content` only —
so a clause whose only inter-version difference was the structural
anchor read as byte-identical even when the alignment pipeline
correctly emitted `MOVED`.

**Fix — full vertical plumb:**

- New migration `0016_clause_numbering_label.py` adds a nullable
  `clauses.numbering_label` column.
- `Clause` ORM model + `ClauseDTO` carry the field; `ClausesRepository`
  picks it up via `from_attributes=True`.
- `_INSERT_CLAUSE_SQL` (in `seed.py` and `poll.py`) writes the label
  from `node.numbering_label`; `PREV_CLAUSES_SQL` reads it back when
  rehydrating the previous tree for the next alignment.
- API `ClauseItem` exposes the field; the webapp TS type mirrors it.
- `ClauseOverlay` flat mode now renders the label as a bold inline-block
  marker above the body, with `data-testid="clause-numbering"`.
  Structure mode is unchanged — the anchor chip already shows the path.

**Test:** `ClauseOverlay.spec.ts → numbering_label` group asserts
flat-mode renders the label when present and omits it when null.

## Sweep

`uv run pytest -m "not integration"` 389 passed, `uv run ruff check` /
`pyright` clean, `uv run pre-commit run --all-files` clean. Webapp
`npm run lint:check && npm run build && npm run test:unit -- --run`
all green (219 tests). Integration tests pass except the
pre-existing `test_seed_curated_set.py` failures already seen on `main`
(unrelated — `documents_inserted` count assertion drifted with the
expanded curated set).

## Demo impact

Before today, the synthetic v2 demo of the MOVED detection would have
been confusing — the box headings were mislabelled and the visible
clause content was identical between the two panes. After today, the
reader can see "**11.** Section 10(2A) …" on the left and "**11A.**
Section 10(2A) …" on the right, and the heading above each box
correctly says "Amendment of section 10 of Principal Act".

## Carry-over for next session

The `ingestion` worker won't repopulate `numbering_label` on existing
rows — the column backfills NULL for everything inserted before the
migration. The staging corpus will need a `scripts/reseed_aca.sh
--yes` after deploy so the curated set carries labels into the
display path. Note this in the deploy runbook handoff.
