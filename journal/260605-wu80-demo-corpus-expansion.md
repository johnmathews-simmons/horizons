# 2026-06-05 — WU8.0: curated-set demo expansion + synthetic v2 staging

Closes Track 8 unit 8.0. Two ships:

1. `data/curated_set.yaml` grown from 6 jurisdictions × 2 sectors to 10
   jurisdictions × 5 sectors. The seed library and CLI are unchanged at
   the type level; the WU3.5 idempotency contract carries the growth.
2. `data/samples/synthetic_v2/` — five hand-authored v2 markdown files
   plus a new `stage_synthetic_v2()` library function that inserts the
   v1+v2 versions, clauses, and alignment-derived change_events. The CLI
   gains a `--stage-synthetic-v2` flag.

The demo now has visible change events without waiting on a live
Lawstronaut walk.

## What shipped

### Curated set (10 jurisdictions × 5 sectors)

`data/curated_set.yaml`:

- Jurisdictions: IE, GB, EU, BE, AT, DE, FR, IT, ES, DK.
- Sectors: financial-services (default), employment, tax,
  consumer-protection, corporate-governance.
- 10 explicit document overrides — one per jurisdiction — mapping each
  fixture's content to a sector that fits the multinational-banking
  compliance demo narrative (FR ACPR → financial-services, GB Foat v
  DWP → employment, ES IVA manual → tax, etc.).
- The IE CRO Social Media Policy keeps its 1-hour cadence override (the
  "always changing" demo signal) and moves to corporate-governance.

`scripts/seed_curated_set.py --dry-run` reports `10 documents / 10
schedule rows`, matching the expanded inventory.

### Synthetic v2 markdown

`data/samples/synthetic_v2/` holds five hand-authored v2 documents
covering one each from IE, GB, FR, DE, and IT (substituting for the
unfulfilled US slot — the fixture inventory has no US capture; the gap
is captured below). Each `v2` is the corresponding `v1` with three
small, realistic edits — one ADD, one MODIFY, one REMOVE — chosen so
the alignment pipeline emits all three change-type families on the
same demo session. The per-file diff intent is in
`data/samples/synthetic_v2/README.md`.

A `--dry-run --stage-synthetic-v2` over the five pairs parses both
versions, runs `horizons_core.core.alignment.align`, and produces:

- 5 documents staged (1 per pair)
- 1773 clause rows (v1 + v2 leaves, summed)
- 32 change_event rows from the alignment pipeline

The 32-event total is real alignment output, not synthetic — every row
the demo renders comes from running the actual matcher on the actual
markdown.

### `stage_synthetic_v2()` in `horizons_ingestion.seed`

New library function plus a `SyntheticV2Pair` and `StagingResult`
dataclass. The function:

- Reads each pair's v1 and v2 markdown.
- Parses both with `horizons_core.core.alignment.parse`.
- Runs `align(v1_tree, v2_tree)` to emit `ChangeEvent` records.
- INSERTs the v1 row in `document_versions` (closed; `valid_to=now`).
- INSERTs the v1 leaves in `clauses` with fresh UUIDs.
- INSERTs the v2 row in `document_versions` (live; `valid_to=NULL`).
- INSERTs the v2 leaves in `clauses` with UIDs that inherit from v1
  paired clauses (mirroring `poll._build_clause_uid_map`).
- INSERTs the alignment events in `change_events` with `before_uid` /
  `after_uid` chosen per change_type (same rules as
  `poll._change_event_insert_rows`).

Idempotency: a document with at least one existing `document_versions`
row is skipped. Re-running the staging is a no-op. A document whose
`documents` row is missing (i.e. WU3.5 hasn't seeded it yet) is reported
via the `warn` callback and skipped.

Dry-run path is genuine — it parses and aligns but performs no DB writes,
returning the same `StagingResult` shape with the to-be-inserted counts.

### CLI shim

`scripts/seed_curated_set.py` gains:

- `--stage-synthetic-v2` — run the v2 staging after the WU3.5 seed step.
- `--synthetic-v2-dir` and `--samples-dir` — override the default
  fixture paths (used in tests; otherwise the defaults are correct).

The discovery helper `_discover_v2_pairs` globs
`data/samples/synthetic_v2/*.md`, regex-matches `<iso>-<docid>-v2.md`,
and pairs each with the corresponding v1 from `data/samples/`. Missing
v1 siblings produce a warning and are skipped.

## Decisions worth remembering

1. **Sample script lives at `scripts/seed_curated_set.py` (repo root),
   not `packages/horizons-ingestion/scripts/`.** The WU3.5 journal
   chose the repo-root location to match `scripts/fetch_fixtures.py`,
   and that decision is load-bearing for the muscle-memory developers
   have built up. The WU8.0 instructions named the package path; we
   stuck with the existing location and confined library logic to
   `horizons_ingestion.seed`. The CLI is a thin shim; testable behaviour
   lives in the library.
2. **Direct v1+v2 staging at seed time, not "let the worker emit
   change events on its next poll".** The worker uses
   `LawstronautClient.get_markdown()` which hits the live API; staging
   a synthetic v2 in the DB does not make the worker emit a change
   event because the live API still returns the real v1 content. The
   only honest read of "visible change events without waiting on
   Lawstronaut" is to emit the events directly at seed time, which is
   what this staging path does. Documents that have synthetic v2s
   staged should not be polled by the worker during the demo — the
   `documents_skipped_already_staged` counter lets the operator
   confirm the staging happened, and the operator pauses the worker
   (or leaves it idle) for the duration of the showcase.
3. **No new migration.** The schema is unchanged. WU8.0 reads and
   writes existing tables only. The append-only triggers permit
   INSERT; the schema_owner role (or the dev superuser) holds the
   grants needed to write into `clauses` and `change_events`.
4. **`v2-synthetic` is the literal version_label.** Keeps the label
   distinguishable in the database from real Lawstronaut v2s (which
   are labelled `v<n>` by the worker). A future cleanup script could
   purge `version_label = 'v2-synthetic'` rows wholesale; the label is
   the seam.
5. **UUID inheritance is identical to `poll._build_clause_uid_map`.**
   Paired clauses inherit, unchanged clauses inherit by path lookup,
   genuinely new clauses get `uuid4()`. The two implementations are
   structurally identical; if either drifts, the seed and the worker
   would produce different identity bookkeeping for the same v1→v2
   transition, which would surface as a regression in the alignment
   regression suite (WU2.4). Cross-link in the docstring keeps this
   visible.
6. **`v1` is closed at `now`, `v2` is live at `now`.** Both rows carry
   the same `valid_from=now`; v1 also has `valid_to=now`. This is the
   simplest valid arrangement: `current_scope()` and the corpus repos
   read the live row by `valid_to IS NULL`, and the regression queries
   filter by `valid_to BETWEEN ... AND ...`. The 1-microsecond
   collision concern is real if a poll runs concurrently with staging,
   but the operational rule "do not run the worker against staged
   documents during the demo" makes the question moot.

## Gap: ~50 documents vs. ~10 actually seeded

The WU8.0 acceptance text targets ~50 documents. The current
`data/samples/fixtures.json` (captured 2026-06-04, 30 entries
round-robin'd across 30 jurisdictions) caps the in-scope seed at ~10 —
one per chosen jurisdiction. Growing to 50 requires re-running
`scripts/fetch_fixtures.py` with a higher `target_count` so each
jurisdiction contributes 4-5 documents.

This was a deliberate choice. Hitting the live Lawstronaut API to grow
the inventory mid-WU8.0 would have stretched the unit; the seed
library already handles a larger inventory without code change
(idempotent inserts of any new YAML rows). The follow-up is to grow
`fixtures.json` in a separate, small unit before the demo — the seed
will pick up the extras automatically.

Tracked here as the WU8.0 follow-up; no Track 8 unit dependency is
affected by the gap.

## Status by suite (end of WU8.0)

- Unit + alignment suites: **323 passed, 4 skipped** (same skips as
  before — small fixtures). No regressions.
- `uv run ruff check .` — clean.
- `uv run pyright` — 0 errors (25 pre-existing warnings unchanged).
- `uv run pre-commit run --all-files` — all hooks pass.
- Smoke: `uv run python scripts/seed_curated_set.py --dry-run` →
  `10 / 10`. `--stage-synthetic-v2 --dry-run` → `5 / 1773 / 32`.

## What's next

- **Grow `fixtures.json` to ~50 entries before the demo.** Re-run
  `scripts/fetch_fixtures.py --target 50` (or extend the script to
  accept `--target` if it doesn't already). The seed will pick up
  every new in-scope row idempotently.
- **WU8.1** (next, this session) — demo accounts CLI + runbook.
- **WU8.2 — Playwright e2e smoke** (already merged; verify it still
  passes after WU8.0 lands a different corpus shape).
- **WU8.3 — demo runbook.** Will reference this journal entry for the
  WU3.5/8.0 staging story.
