# Curated-set bootstrap

*Last revised: 2026-06-07.*
*Path: docs/runbooks/seeding.md.*

How `documents` and `document_poll_schedule` get their initial rows so the
ingestion worker has something to poll. Implementation lives at
`scripts/seed_curated_set.py` (CLI shim) plus
`packages/horizons-ingestion/src/horizons_ingestion/seed.py` (library).
WU3.5 ships the starter set; WU8.0 adds the synthetic-v2 staging path;
WU8.5 expands the seed to the full 31-fixture inventory; WU8.7 pivots
the demo relabel cluster to native GB / EU sources so the UK and EU
demo subscriptions are visibly populated.

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
sectors: [BANKING, employment]             # allowed sectors; sectors[0] is the default
default_cadence_hours: 24                  # cadence applied to documents without an override

documents:                                  # optional per-document overrides
  - id: "8064194"                           # lawstronaut_document_id (string)
    cadence_hours: 1                        # "always changing" demo cadence
  - id: "19194112"
    sector: employment                      # override default sector
  - id: "28914588"
    jurisdiction: UK                        # relabel a GB capture as UK for the demo
    sector: BANKING
```

Top-level keys:

- `jurisdictions` — ISO codes. Fixtures with `iso` outside this list are skipped. This is the **fixture-iso filter**, not the output taxonomy — a per-doc `jurisdiction:` override can relabel a passed-through fixture to any token (e.g. `UK`, `EU`) without that token appearing here.
- `sectors` — allowed sector taxonomy values. The first entry is the default for any document without an explicit `sector:` override. Convention is the canonical taxonomy from `schema.md` (`BANKING`, `INSURANCE`, …).
- `default_cadence_hours` — `document_poll_schedule.cadence_interval` for documents without a `cadence_hours:` override.

Per-document overrides under `documents:`:

- `id` (required) — matches a fixture's `document_id`. If no fixture has that id, the entry is reported on stderr and skipped.
- `cadence_hours` (optional) — overrides the top-level default.
- `sector` (optional) — overrides `sectors[0]`. Must appear in the top-level `sectors:` list.
- `title` (optional) — overrides the fixture's `title`. Useful when the upstream title is unhelpful.
- `jurisdiction` (optional) — replaces the fixture's `iso` on the seeded row. Free-form; not validated against the top-level `jurisdictions` list. Used to map a fixture captured under one ISO-2 (e.g. `GB`) onto the demo's user-facing token (e.g. `UK`) without forking the upstream capture.

Documents not listed under `documents:` get the defaults applied automatically; the per-doc list is for overrides only, not for opt-in. To include every IE/GB/EU fixture as BANKING / 24h, omit the `documents:` list entirely.

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
curated set. WU8.5's expansion to the full 31-fixture inventory would
otherwise produce a 31-doc burst on the first tick.

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

## WU8.0 / WU8.5 / WU8.7 — demo corpus + synthetic v2 staging

WU8.0 added the synthetic v2 staging path used by the demo's headline
clause-diff moment. WU8.5 expanded the seed to the full 31-fixture
inventory under `data/samples/`. WU8.7 reworked the relabel cluster
so the UK and EU demo subscriptions are visibly populated from native
GB / EU sources (see `data/curated_set.yaml` header for the current
list of relabel decisions). Each demo subscription (UK/BANKING and
EU/BANKING) now sees ≥ 10 documents.

The two sectors carried in `data/curated_set.yaml` are `BANKING` and
`employment`. Honest-sector documents (no relabel) stay on their
native sector so the admin view still surfaces the taxonomy mix.

The v2 injection path lives in this same module rather than a separate
admin tool:

- `data/samples/synthetic_v2/<iso>-<docid>-v2.md` — hand-authored
  revisions of selected v1 fixtures, each with one or more of ADDED /
  MODIFIED / REMOVED / MOVED so the alignment pipeline emits a
  realistic editorial-intent mix. Currently 8 pairs (au, de, eu, fr,
  gb, ie-8064194, ie-27732019, it). See
  `data/samples/synthetic_v2/README.md` for the per-document diff
  intent.
- `horizons_ingestion.seed.stage_synthetic_v2(pairs, …)` — for each
  `(lawstronaut_document_id, v1_path, v2_path)` pair, inserts the v1
  row in `document_versions` (closed at `now`), the v1 leaves in
  `clauses` with fresh UUIDs, the v2 row in `document_versions`
  (live; `valid_to=NULL`), the v2 leaves in `clauses` with UIDs
  inherited via the same rules as
  `horizons_ingestion.poll._build_clause_uid_map`, and the
  alignment-derived `change_events`. Idempotent per document — rows
  whose `document_versions` already has entries are skipped.
- `scripts/seed_curated_set.py --stage-synthetic-v2` runs the WU3.5
  seed and then the v2 staging in a single invocation.

**Operational rule:** the worker must not poll documents that have
staged synthetic v2s. The worker uses Lawstronaut HTTP, which returns
the real v1 — a sha mismatch with the staged v2 would push a spurious
v3. **This is enforced in code:** `stage_synthetic_v2` parks the staged
documents' `document_poll_schedule.next_poll_at` at `2026-12-31` so the
claim loop's `next_poll_at <= now()` predicate never selects them. See
`journal/260605-fix-worker-staged-guard-and-env-validation.md` and the
`docs/runbooks/demo.md` pre-demo checklist for the operator-side
confirmation.

**Demo accounts** are a separate concern handled by
`packages/horizons-api/scripts/create_demo_accounts.py`. See
`docs/runbooks/demo-accounts.md` for the operator-facing steps.

## Related

- `packages/horizons-core/src/horizons_core/db/schema.md` §`documents` and §`document_poll_schedule` — the row shapes this script writes.
- `data/samples/README.md` — the fixture-inventory provenance and refresh cadence.
- `docs/RFC-4 services.md` §Ingestion service — where this substrate fits in the worker's lifecycle.
