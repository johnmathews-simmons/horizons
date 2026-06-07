# 2026-06-05 — WU3.5: bootstrap script seeds the curated set

*Last revised: 2026-06-05.*
*Path: journal/260605-wu35-curated-set-seed.md.*

Ships the substrate the WU3.3 claim loop polls: rows in `documents` and
matching rows in `document_poll_schedule`, seeded from
`data/samples/fixtures.json` + a hand-curated `data/curated_set.yaml`.
Closes Track 3 unit 3.5. Demo (~2026-06-08) now has documents to ingest
without waiting on a live Lawstronaut walk.

## What landed

- **Library** `packages/horizons-ingestion/src/horizons_ingestion/seed.py`
  — frozen dataclasses (`CuratedSet`, `DocOverride`, `PendingRow`,
  `SeedRow`, `SeedResult`), `parse_curated_set()`, `select()`,
  `stagger()`, and `run_seed()`. Library boundary, not a class — the
  pieces compose for the integration tests, and the script is a thin
  shim.
- **CLI shim** `scripts/seed_curated_set.py` — `argparse` flags
  (`--curated`, `--fixtures`, `--dry-run`); reads `HORIZONS_DB_URL`;
  loads YAML + JSON; calls `run_seed`; prints counts.
- **Starter YAML** `data/curated_set.yaml` — 6 jurisdictions (IE, GB,
  EU, BE, AT, DE) × 2 sectors (financial-services, employment). One
  doc on a 1h cadence (the "always changing" demo signal); the rest
  on 24h. Three per-document overrides demonstrating the cadence /
  sector / title knobs.
- **Reference doc** `docs/seeding.md` — anchor-style: what the script
  does, the YAML schema, idempotency contract, stagger algorithm,
  and the WU8.0 hand-off plan.
- **Tests** — 17 unit tests (no DB) at
  `packages/horizons-ingestion/tests/test_seed_helpers.py`, 6
  integration tests (testcontainers Postgres) at
  `tests/integration/test_seed_curated_set.py`. Full sweep is **464
  passed** (was 441 before WU3.5; +23).

## Decisions locked at the start

Asked via `AskUserQuestion` before the first edit. All four
recommendations accepted as-is:

1. **YAML schema = hybrid.** Top-level `jurisdictions` / `sectors` /
   `default_cadence_hours` plus an optional `documents:` list for
   per-doc overrides. Avoids the choice between "scope filter only"
   (too coarse — no way to mark the hourly demo doc) and "explicit
   list only" (too verbose at WU8.0 scale).
2. **No `iso`+`portal` columns on the schedule.** Deferred. The
   `get_markdown` URL-form decision left over from WU3.4 hasn't broken
   yet; pre-building isn't justified. If the fallback turns out
   necessary, add a column in a later migration.
3. **Cadence default = 24h, stagger evenly within each cadence
   bucket.** Avoids the initial-tick burst that "all immediately due"
   would produce. At WU8.0's ~50-doc scale this matters more than at
   WU3.5's starter scale.
4. **Script location = `scripts/seed_curated_set.py`.** Matches
   `scripts/fetch_fixtures.py`. The testable library logic still lives
   in `horizons_ingestion.seed`; the script is a thin CLI shim, so
   `scripts/` exclusion from ruff/pyright doesn't hide implementation
   bugs.

## Things worth remembering

- **Raw-SQL-isolation guard now has a second exemption.** The
  architectural test that pins `text(...)` calls to
  `horizons_core.db.session` learned about
  `horizons_ingestion.seed` as a bootstrap-time DDL-owner code path.
  Documented in the test's `ALLOWED_FILES` block. This is the second
  legitimate carve-out (session.py was the first); future modules
  should still go through the session bracket — the seed is
  bootstrap, not application code.
- **Idempotency uses `ON CONFLICT DO NOTHING` on both tables.**
  `documents.UNIQUE(lawstronaut_document_id)` and
  `document_poll_schedule.PK = document_id` are the levers. The
  seed re-fetches `documents.id` on the conflict path to drive the
  schedule-row insert deterministically, so re-runs with the YAML
  *extended* pick up only the new entries — verified by
  `test_seed_idempotent_after_partial_state`.
- **`documents` is append-only by trigger.** The seed cannot
  retroactively change a curated doc's title or sector once seeded.
  Documented in `docs/seeding.md` with the implication: changing a
  YAML title and re-seeding silently leaves the existing row's title
  intact. Correcting curation metadata is admin-tool territory (out
  of WU3.5 scope).
- **The IE Companies Act 2014 (`ie-27732019`) is on disk but not in
  `fixtures.json`.** It was collected separately as the original
  Irish Statute Book Act, while `fixtures.json` is the round-robin
  capture of 30. The seed reads only `fixtures.json`, so the curated
  set picks `ie-8064194` (CRO policy) as the IE demo signal. If
  WU8.0 wants the dense Companies Act in the demo corpus, it'll need
  to extend `fixtures.json` first.
- **`pytest.approx` is still a pyright-strict trap.** Recurring
  gotcha from WU3.4. Used `float(...) == expected` in the integration
  test instead.
- **`extract(epoch from interval)` returns `Decimal`, not float.**
  Hit in the first run of the cadence-window test. Convert with
  `float(...)` before constructing a `timedelta(seconds=...)`.

## Carry-overs / next session

1. **`get_markdown` URL-form fallback decision** — still deferred from
   WU3.4 / WU3.5. The first live `get_markdown` call against the demo
   corpus may need iso+portal+paginate fallback. Probe to add: see
   journal entry `260605-wu34-poll-transaction.md` §"Next session".
2. **WU8.0 — curated-set demo expansion.** Grow `data/curated_set.yaml`
   to ~50 documents × 10 jurisdictions × 5 sectors and stage
   synthesised "v2" documents via a separate admin tool. WU3.5's seed
   stays idempotent re-runner across that growth — verified by the
   integration tests. The WU8.0 hand-off contract is spelled out in
   `docs/seeding.md`.
3. **Orphan-blob sweep grace window** — micro-fix carried from WU3.4;
   independent of WU3.5.
4. **WU3.6** — next in Track 3 if any.

Branch: `worktree-eng-wu3.5-curated-set-seed` → `main` via ff-merge.
