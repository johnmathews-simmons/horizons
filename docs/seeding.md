# Curated-set bootstrap

How `documents` and `document_poll_schedule` get their initial rows so the
ingestion worker has something to poll. Implementation lives at
`scripts/seed_curated_set.py` (CLI shim) plus
`packages/horizons-ingestion/src/horizons_ingestion/seed.py` (library).
WU3.5 ships the starter set; WU8.0 grows it for the demo.

## What it does

Reads two files, writes two tables.

| Input | Role |
| --- | --- |
| `data/samples/fixtures.json` | Authoritative inventory of upstream documents fetched by `scripts/fetch_fixtures.py`. Provides `iso`, `document_id`, `title`. |
| `data/curated_set.yaml` | Curation policy: which jurisdictions to seed, the default sector and cadence, and per-document overrides. |

For each fixture whose `iso` matches a jurisdiction in the YAML, the seed
INSERTs one row into `documents` and one row into `document_poll_schedule`.
`lawstronaut_document_id` is the upstream key — the table carries a
`UNIQUE` constraint on it, so re-running the seed is a no-op on already-seeded
documents (`ON CONFLICT (lawstronaut_document_id) DO NOTHING`).

## YAML schema

```yaml
# data/curated_set.yaml
jurisdictions: [IE, GB, EU, BE, AT, DE]    # ISO codes; only fixtures with these iso values are seeded
sectors: [financial-services, employment]  # allowed sectors; sectors[0] is the default
default_cadence_hours: 24                  # cadence applied to documents without an override

documents:                                  # optional per-document overrides
  - id: "8064194"                           # lawstronaut_document_id (string)
    cadence_hours: 1                        # "always changing" demo cadence
  - id: "19194112"
    sector: employment                      # override default sector
```

Top-level keys:

- `jurisdictions` — ISO codes. Fixtures with `iso` outside this list are skipped.
- `sectors` — allowed sector taxonomy values. The first entry is the default for any document without an explicit `sector:` override.
- `default_cadence_hours` — `document_poll_schedule.cadence_interval` for documents without a `cadence_hours:` override.

Per-document overrides under `documents:`:

- `id` (required) — matches a fixture's `document_id`. If no fixture has that id, the entry is reported on stderr and skipped.
- `cadence_hours` (optional) — overrides the top-level default.
- `sector` (optional) — overrides `sectors[0]`. Must appear in the top-level `sectors:` list.
- `title` (optional) — overrides the fixture's `title`. Useful when the upstream title is unhelpful.

Documents not listed under `documents:` get the defaults applied automatically; the per-doc list is for overrides only, not for opt-in. To include every IE/GB/EU fixture as financial-services / 24h, omit the `documents:` list entirely.

## Idempotency

Re-running the seed produces zero new rows when the YAML is unchanged. The
levers are:

- `documents` carries `UNIQUE(lawstronaut_document_id)`. The seed uses `INSERT ... ON CONFLICT (lawstronaut_document_id) DO NOTHING`.
- `document_poll_schedule.PK` is `document_id`. The seed uses `INSERT ... ON CONFLICT (document_id) DO NOTHING`.

`documents` is append-only via trigger (rejects every `UPDATE`), so the seed
cannot mutate a row's `title` or `sector` after it lands. Changing a curated
document's title in the YAML and re-running the seed leaves the existing row
untouched. To correct curation metadata: end the row (out of scope for
WU3.5; admin tooling will surface this in a later WU) and re-seed.

## Stagger algorithm

All seeded `next_poll_at` values are in `[now, now + cadence)`, distributed
evenly within each cadence bucket. Documents with `cadence_hours=24` and N
entries share the 24h window with offsets `0, 24/N, 48/N, ...`. Documents
with `cadence_hours=1` get a separate 1h stagger.

This matches the production-shape goal: the first claim-loop tick after a
fresh seed claims at most one document per cadence bucket, not the entire
curated set. WU8.0's expansion to ~50 documents would otherwise produce a
50-doc burst on the first tick.

## Running

```bash
export HORIZONS_DB_URL=postgresql://...        # same DSN Alembic uses
uv run scripts/seed_curated_set.py             # idempotent; safe to repeat
uv run scripts/seed_curated_set.py --dry-run   # print plan; insert nothing
```

The script exits non-zero on:

- Missing `data/curated_set.yaml`.
- YAML schema validation failure (unknown keys, unknown sector reference, etc.).
- Postgres connection failure.

It exits zero on:

- A successful run (newly inserted rows reported, with counts).
- A re-run where nothing is new (zero inserts reported).

## WU8.0 hand-off

The curated-set bootstrap is the substrate WU8.0 grows for the demo period.
WU8.0 expands `data/curated_set.yaml` to roughly 50 documents × 10
jurisdictions × 5 sectors and stages synthesised v2 documents — the
"always changing" demo signal — through a separate admin tool, not through
this script. WU3.5's responsibility ends at `(documents, document_poll_schedule)`
rows for the v1 corpus; WU8.0 owns the v2 injection path that produces
visible `change_events` without waiting on Lawstronaut.

The hand-off contract: WU8.0's admin tool reads the same `data/curated_set.yaml`
as a source of truth for which documents exist in the demo corpus. The seed
script does not need to know about WU8.0 — its idempotency means WU8.0 can
re-run it before the demo to top up any documents the operator added to the
YAML after the original seed.

## Related

- `packages/horizons-core/src/horizons_core/db/schema.md` §`documents` and §`document_poll_schedule` — the row shapes this script writes.
- `data/samples/README.md` — the fixture-inventory provenance and refresh cadence.
- `docs/4. services.md` §Ingestion service — where this substrate fits in the worker's lifecycle.
