# Pipeline overview: change events

*Last revised: 2026-06-07.*
*Path: docs/pipeline-overview.md.*

A walk-through of how a document on the Lawstronaut API becomes one or more clause-level change events in our database. Each step links to the doc that owns it. This page is a map; it does not re-explain anything its sources already explain.

The pipeline runs in one container — the ingestion worker — driven by a per-document schedule in Postgres. The shape of that container (long-running asyncio, not cron-once) is fixed by [ADR-0001](./adrs/0001-worker-shape.md). The wider why-three-services context lives in [`RFC-4 services.md`](./RFC-4%20services.md).

## 1. The seven steps

```
schedule → claim → fetch → hash → branch
                                  ├── unchanged: extend valid_to
                                  └── changed:   blob + parse + align + emit → housekeeping
```

### 1.1 Schedule

`document_poll_schedule` is a Postgres table (created in migration `0007_ingestion_tables`) with one row per polled document. Its load-bearing columns are `next_poll_at` (when the row becomes claimable), `cadence_interval` (how far ahead to push `next_poll_at` after a successful poll), `last_polled_at`, and `failure_count`. Cadence is per-row; the default of 24 h is configured in `data/curated_set.yaml` (`default_cadence_hours: 24`) and applied at seed time. One demo-heartbeat document — IE CRO Social Media Policy, id `8064194` — is overridden to `cadence_hours: 1` so the claim loop touches it every demo session; every other seeded document is on the 24 h default. The schedule's wider role in the data model is in [`RFC-3 database-design.md`](./RFC-3%20database-design.md).

**How rows get into the schedule.** The worker never inserts here — it only polls rows that already exist. New rows land via the seed library (`packages/horizons-ingestion/src/horizons_ingestion/seed.py`, driven by the `scripts/seed_curated_set.py` CLI shim) which reads two files and writes two tables:

| Input | Effect |
|---|---|
| `data/samples/fixtures.json` (built by `scripts/fetch_fixtures.py` from the live Lawstronaut inventory) | One candidate document per entry. |
| `data/curated_set.yaml` (curation policy: which jurisdictions to include, sector mapping, per-document cadence overrides) | Decides which candidates get seeded and with what cadence. |

For each fixture matching the YAML's jurisdiction filter, the seed runs one `INSERT ... ON CONFLICT (lawstronaut_document_id) DO NOTHING` into `documents` and one `INSERT ... ON CONFLICT (document_id) DO NOTHING` into `document_poll_schedule` (each table conflicts on its own natural / primary key). Both `DO NOTHING` clauses make the seed idempotent.

**Not demo-only.** The seed is the *only* onboarding path today — the same script runs at first deploy, on every subsequent deploy (idempotent), and any time the curated set is extended. Adding a new document means appending it to `fixtures.json` (typically by running `fetch_fixtures.py` against a Lawstronaut id) and either adding its jurisdiction to the YAML or naming it explicitly under `documents:`. No redeploy is required if the fixtures inventory is reachable to the seed process; this is the "configuration over code for sources" rule from CLAUDE.md.

**What the worker does *not* do.** It does not discover new documents on its own, does not crawl new portals, and does not write to `documents` or `document_poll_schedule`. Discovery-on-the-worker is a forward-looking design option ([`RFC-4 services.md`](./RFC-4%20services.md) §"Ingestion service") but is not implemented; everything in production today goes through the seed flow.

Runbook for the seed (YAML schema, idempotency contract, demo-time `--stage-synthetic-v2` flag): [`docs/runbooks/seeding.md`](./runbooks/seeding.md).

### 1.2 Claim

Every tick (50 ms by default), the worker runs `SELECT … FOR UPDATE SKIP LOCKED LIMIT N` against `document_poll_schedule` for rows whose `next_poll_at <= now()` and `failure_count <= threshold`. The lock is held through the rest of the steps so the poll's writes commit atomically with the schedule update. Owned by [`packages/horizons-ingestion/src/horizons_ingestion/loop.md`](../packages/horizons-ingestion/src/horizons_ingestion/loop.md).

### 1.3 Fetch

For each claimed `document_id`, the worker calls `client.get_markdown(document_id)` against Lawstronaut's `/v2/contents/markdown` — bearer-token authenticated, with the documented quirks (`content_markdown` field, string-or-int IDs, malformed `T00:00:000Z` dates) handled by the client. Lawstronaut surface and gotchas are in [`docs/api/lawstronaut-endpoints.md`](./api/lawstronaut-endpoints.md) and [`docs/api/operational-notes.md`](./api/operational-notes.md).

Empty `data` → log and return; the schedule row gets bumped normally. HTTP error → the tick wraps the call and bumps `failure_count`; once `failure_count` exceeds the threshold (default 5, so the sixth failure trips it) the worker writes an `ingestion_incident` row with `error_class = 'parked'` and the claim query then filters the row out of future ticks.

**Viewing the logs.** Locally: stdout of `uv run python -m horizons_ingestion`. Live tail in Azure: `az containerapp logs show -n horizons-dev-worker -g horizons-nonprod --follow --tail 200` (streams from the ACA control plane; works even when Log Analytics isn't wired). Historical / queryable: Log Analytics workspace `horizons-dev-law` — KQL against `ContainerAppConsoleLogs_CL` filtered to `ContainerName_s == "horizons-dev-worker"`. For ACA Jobs (migrate, reseed), substitute `az containerapp job logs show --container <name> --execution <id>`.

### 1.4 Hash and branch

`sha256(markdown)` is computed once and compared against the current live version's `content_sha256` (`SELECT … WHERE document_id = $1 AND valid_to IS NULL`). Two paths from here. Both paths are owned by [`packages/horizons-ingestion/src/horizons_ingestion/poll.md`](../packages/horizons-ingestion/src/horizons_ingestion/poll.md).

### 1.5 Unchanged path — "registering an absence"

Hash matches the live row: one statement, `UPDATE document_versions SET valid_to = $now WHERE id = $live.id`. No new version, no new clauses, no change events. The extended `valid_to` is the durable record that we *looked* and saw no change — answering the temporal primitive's "what did this document look like on date X" question continuously, not just at version boundaries. The append-only trigger on `document_versions` (`reject_document_version_update`, created in migration `0003`) is narrowed by migration `0007` to permit `valid_to`-only updates; everything else is still rejected. Tick commits, next document.

### 1.6 Changed path — emitting events

Hash differs (or no live row exists — first-ever poll). The worker:

1. **Uploads the original.** `blob_store.put("<sha256>.md", markdown)` — the entire document body, as one content-addressed blob, into the `originals` Azure Blob Storage container. "Original" here means the markdown Lawstronaut handed us byte-for-byte — we never talk to the upstream legal portals directly; Lawstronaut is the aggregator that crawls them and converts to markdown on our behalf. Each new content version writes a new blob; identical content de-duplicates for free via the sha256 key. Nothing collapses the chain — every version's blob is retained; `sweep.py` only reclaims *orphans* (blobs no `document_versions` row references). The put runs while the tick's Postgres transaction is open but is not atomic with it (object storage has no two-phase commit), so a poll that raises between the put and the row inserts leaves an orphan; `sweep.py` reclaims those on a slow loop (every 30 min).

2. **Parses the new markdown into a `Clause` tree.** Markdown → tree, with heading-anchored clause paths. The parser is the substrate the rest of the pipeline assumes; spec is [`docs/5. clause-tree-parser.md`](./5.%20clause-tree-parser.md).

3. **Reconstructs the predecessor's tree from the `clauses` table** (not from re-parsing the previous blob — cheaper, and preserves the parser config that was in force at the time). On the very first poll for a document there is no predecessor; `_initial_events` then emits one synthetic `ADDED` event per non-empty leaf at confidence `1.0` and step 4 is skipped.

4. **Aligns predecessor → successor.** Shingling + MinHash + LSH + Jaccard against `tuning.similarity_threshold` produce a per-clause pairing, classified as `ADDED` / `REMOVED` / `MODIFIED` / `MOVED`. The full alignment design — identity invariant, three-pass similarity stack (source IDs → heading-anchored → LSH + monotonic DP), threshold rationale — is [`docs/RFC-2 clause-alignment.md`](./RFC-2%20clause-alignment.md).

   Pass 2 (heading-anchored) is order-blind: a clause that moves to a new parent but keeps its own semantic heading is paired regardless of distance. The monotonic DP only governs the residual where the heading was also renamed (or never existed, e.g. unheaded `(a)` / `(i)` leaves). The pathological case — cross-section move *and* heading rename *and* out-of-monotonic-order — falls through both passes, emits `REMOVED` + `ADDED` instead of `MOVED` / `MODIFIED`, and breaks the `clause_uid` chain; RFC-2 §"Known limitations" accepts this.

   Reliability is measured by the calibration suite at `tests/alignment/test_fixtures.py` (RFC-2 §"Calibration"): per-fixture identity case (`align(v1, v1) == []` — strict, any non-empty result fails the build) and four-mutation case (precision / recall / F1 against a synthesised ADDED + REMOVED + MODIFIED + MOVED).

   Run `uv run pytest tests/alignment/test_fixtures.py` to get the per-fixture scoreline; the table is emitted by the session terminal summary in `tests/alignment/conftest.py` regardless of `-v`.

   To sweep `shingle_k` / `signature_size`, the YAML in `tuning_configs/_default.yaml` is *not* load-bearing on its own — the suite calls `align(v1, v2)` without an explicit `tuning=`, which falls back to `default_tuning_config()` (the `TuningConfig` class field defaults in `packages/horizons-core/src/horizons_core/core/alignment/tuning.py`). To actually move the numbers either (a) edit the `Field(default=…)` values in `tuning.py`, or (b) patch the test to pass `tuning=load_tuning_config("_default")` so the YAML becomes the source of truth, or (c) write a one-off script that builds `TuningConfig(shingle_k=…, signature_size=…)` and runs `align` over the corpus directly.

   Known degradations recorded in RFC-2: boilerplate-rich corpora (AL 3.8 MB, LV 58 KB) keep recall at 1.0 but collapse precision to ~0.1; CJK content under-shingles on whitespace tokenisation.

5. **Materialises clause UIDs.** A `clause_uid` is the stable name of a clause across time — what lets the API answer "show me the history of *this* clause" even after the document renumbers. When a new version lands, every clause in the new tree needs a UID; we either inherit one (the aligner says it's the same clause we saw before, just possibly moved or reworded) or mint a fresh `uuid4()` (genuinely new). The pairing logic drives the inheritance: three cases in `poll.py` `_build_clause_uid_map` — (a) the aligner paired the after-side clause with a before-side clause → inherit that clause's `clause_uid` (carries identity across MOVED and MODIFIED, including renumbering); (b) same path exists in the predecessor and the aligner emitted no event → inherit by direct path lookup (carries identity for the boring unchanged majority); (c) genuinely new → mint a fresh `uuid4()`. The "same `clause_uid` across versions" invariant from RFC-2 is what makes the history query cheap (a `WHERE clause_uid = $1` scan, no per-query parsing).

6. **Writes the four-table batch atomically:** one `INSERT` into `document_versions` (returning the new id), an `executemany` `INSERT` into `clauses` (one row per new-tree leaf), an `executemany` `INSERT` into `change_events` (one row per emitted event), and — if a predecessor existed — an `UPDATE document_versions` closing the predecessor's `valid_to`. `poll_document` itself does not open a transaction; everything runs on the connection that holds the claim lock and commits with the tick's `COMMIT`.

### 1.7 Schedule housekeeping

After the poll body returns, the tick updates `document_poll_schedule`: success bumps `next_poll_at` by `cadence_interval` and clears `failure_count`; exception increments `failure_count` and may park the row. `last_tick_at` is stamped at the end of the tick; the `/healthz` probe reads it.

### 1.8 The events become user-facing

Once committed, `change_events` are queryable by the API. The discovery primitive lives at `GET /v1/discovery` (not `/v1/changes` — that's the SPA *route*, served by `ChangesView.vue` and backed by the same `/v1/discovery` endpoint). The three primitives (discovery, temporal, differential) and their scope/filter dimensions are [`RFC-1 product-questions.md`](./RFC-1%20product-questions.md); the API surface for them is [`docs/api/horizons-primitives.md`](./api/horizons-primitives.md).

## 2. Multi-tenant gating (cross-cutting)

The pipeline writes corpus rows without tenant awareness — `change_events` carry `jurisdiction` and `sector`, but no `tenant_id`. Subscription scoping happens at *read* time, in the API's repository layer with an RLS scope policy as the second layer. Why corpus access is scoped this way is [`RFC-4 services.md` §"Multi-tenant isolation"](./RFC-4%20services.md).

## 3. What this overview does not cover

- The webapp's rendering of change events (SPA-side concern; lives in `packages/horizons-webapp/`).
- The deploy pipeline that gets the worker into Azure Container Apps. See [`docs/runbooks/deploy.md`](./runbooks/deploy.md).
- Tuning the alignment parameters (`shingle_k`, `signature_size`, `similarity_threshold`). Surfaced as runtime config per "configuration over code"; spec in RFC-2. The measurement seam — `tests/alignment/test_fixtures.py` — is described in §1.6 step 4.

## 4. Source-of-truth index

| Step | Owner doc |
|---|---|
| Worker container shape | [`ADR-0001`](./adrs/0001-worker-shape.md) |
| Service responsibilities and isolation | [`RFC-4 services.md`](./RFC-4%20services.md) |
| Schedule, claim loop, tick anatomy | [`loop.md`](../packages/horizons-ingestion/src/horizons_ingestion/loop.md) |
| Per-document poll body (fetch → hash → branch → emit) | [`poll.md`](../packages/horizons-ingestion/src/horizons_ingestion/poll.md) |
| Markdown → `Clause` tree | [`5. clause-tree-parser.md`](./5.%20clause-tree-parser.md) |
| Clause identity, alignment, change classification | [`RFC-2 clause-alignment.md`](./RFC-2%20clause-alignment.md) |
| Database invariants the pipeline writes against | [`RFC-3 database-design.md`](./RFC-3%20database-design.md) |
| What the customer asks the API for | [`RFC-1 product-questions.md`](./RFC-1%20product-questions.md) |
| Lawstronaut surface and quirks | [`docs/api/lawstronaut-endpoints.md`](./api/lawstronaut-endpoints.md), [`operational-notes.md`](./api/operational-notes.md) |
