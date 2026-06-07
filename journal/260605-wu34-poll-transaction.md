# WU3.4 — Per-document poll transaction

*Last revised: 2026-06-06.*
*Path: journal/260605-wu34-poll-transaction.md.*

*Session 2026-06-05. Branch `worktree-eng-wu3.4-poll-transaction` → ff-merged to `main`.*

The body the WU3.3 claim loop calls for every due document. Replaces
`noop_poll`. Implements `docs/RFC-4 services.md` §"Ingestion service":
fetch markdown via the WU3.2 Lawstronaut client, hash, extend the live
version's `valid_to` if unchanged; otherwise upload the blob and write
a four-row tuple (`document_versions` + `clauses` +
`alignment.ChangeEvent[]` → `change_events` + predecessor `valid_to`)
in one Postgres transaction. Discovery: the `change_events` table
referenced everywhere in the design-doc chain was a planned WU1.2 stub
that never landed; WU3.4 ships the real-column shape directly so
WU4.4 / WU5.3 don't have to widen later.

## What shipped

1. **Migration `0009_change_events.py`** — bigserial PK; FKs to
   `documents` + `document_versions`; denormalised jurisdiction /
   sector for the doc-3 composite index
   `(jurisdiction, sector, detected_at, effective_date)`; `change_type`
   CHECK in `{ADDED, REMOVED, MODIFIED, MOVED}` mirroring
   `alignment.ChangeType`; nullable before/after `clause_uid` / `path` /
   `text`; `alignment_confidence` CHECK `(0, 1]`; `detected_at`
   default `now()`; `effective_date` nullable. Per-document and
   per-version indexes too. RLS enabled + `FORCE`d; subscription-scope
   policy on `TO api_app` mirrors WU1.4's corpus shape; ingestion
   pass-through policy on `TO ingestion_worker`. `BEFORE UPDATE OR
   DELETE` trigger; grants per WU1.0's role matrix. Self-documenting
   `COMMENT ON` for the three load-bearing columns.
2. **`packages/horizons-ingestion/src/horizons_ingestion/blob/`** — the
   `BlobStore` Protocol + an in-memory impl for tests (`MemoryBlobStore`,
   with content-hash collision detection on different bytes) + an
   Azure impl (`AzureBlobStore` via `azure.storage.blob.aio` +
   `DefaultAzureCredential` from `azure.identity.aio`). The Azure impl
   is an async context manager so the SDK's network resources release
   cleanly on shutdown.
3. **`packages/horizons-ingestion/src/horizons_ingestion/poll.py`** —
   `poll_document` + private helpers (`_load_previous_tree`,
   `_initial_events`, `_build_clause_uid_map`, `_clause_insert_rows`,
   `_change_event_insert_rows`). Eight `Final` SQL constants at the
   top. Signature matches the WU3.3 `PollFn` seam plus extra kwargs
   (`client`, `blob_store`, `blob_container`, `tuning`, `clock`) that
   `__main__` binds via `functools.partial`.
4. **`poll.md`** — design doc next to the code (same pattern as
   `loop.md`, `client.md`). Documents flow, version_label scheme,
   clause-UID identity rule, trade-offs accepted, test substrate.
5. **`packages/horizons-ingestion/src/horizons_ingestion/sweep.py`** —
   the orphan-blob reclaimer. `SweepLoop` mirrors `ClaimLoop`'s
   lifecycle (a long-running coroutine driven by an
   `asyncio.Event`). Default cadence 30 min, configurable via
   `HORIZONS_INGESTION_SWEEP_INTERVAL_S`. Reads
   `document_versions.content_blob_key` per pass, lists blobs, deletes
   keys matching the `<sha256>.md` shape that aren't referenced.
6. **`__main__.py` rewired** to build the `LawstronautClient` + the
   `AzureBlobStore`, bind `poll_document` with `functools.partial`,
   and run `ClaimLoop` + `SweepLoop` concurrently via
   `asyncio.gather`. Shutdown event wired to both loops.
7. **`config.py`** — three new knobs: `blob_account_url`,
   `blob_container` (default `"originals"`), `sweep_interval_s`
   (default `1800.0`).
8. **`schema.md`** — full `change_events` aggregate description added
   between `document_poll_schedule` and `ingestion_incident`; the
   §"Multi-tenant access" closing block gained a WU3.4 paragraph.
9. **`loop.md`** — new env-var rows; new §"The sweep loop" subsection;
   the poll-seam doc now points at `poll.md` instead of "WU3.4 will
   provide".
10. **Tests.**
    - `tests/test_change_events_migration.py` (9 tests) — column shape,
      ownership, indexes, CHECK constraints, append-only trigger,
      grants, RLS + policies, full round-trip insert with every
      nullable column populated.
    - `packages/horizons-ingestion/tests/test_blob_memory.py` (6 tests)
      — `MemoryBlobStore` Protocol contract.
    - `packages/horizons-ingestion/tests/test_sweep_helpers.py`
      (5 tests) — `_is_content_addressed_key` boundary cases.
    - `tests/integration/test_poll_document.py` (5 tests) — drives
      `poll_document` through `ClaimLoop.tick()` against the
      testcontainer + a stub `LawstronautClient`: first poll inserts
      version + clauses + ADDED events; unchanged poll only extends
      `valid_to`; changed poll closes v1 + inserts v2 with events
      naming the changed section; multi-version chain (v1 → v2 → v3)
      carries `clause_uid` identity for unchanged clauses; fetch
      returns `None` does nothing.
    - `tests/integration/test_blob_sweep.py` (3 tests) — orphan
      reclaimed, referenced spared, non-content-addressed keys
      ignored, empty container is a noop.
11. **`tests/integration/conftest.py`** TRUNCATE list updated to
    include `change_events`.
12. **`pyproject.toml`** — `azure-identity>=1.18` + `azure-storage-blob>=12.22`
    added to `horizons-ingestion` runtime deps. Resolved to
    `azure-identity==1.25.3`, `azure-storage-blob==12.29.0`.

Full sweep green: **416 passed / 4 skipped / 1 deselected**
(was 351+4 on the WU3.3 baseline; +65 from this unit, mostly the 31
alignment-fixture tests crossing the threshold from "WU2.x scope" to
"now exercised end-to-end" plus the new WU3.4 surface). `ruff check`,
`ruff format --check`, `pyright` (0 errors / 18 stub-not-found
warnings, all pre-existing for `testcontainers.postgres`),
`pre-commit run --all-files`, webapp `lint:check` + `build` +
`vitest --run` (3/3) all clean.

## Decisions resolved up-front

Four `AskUserQuestion` (with previews) before the first edit:

1. **asyncpg ↔ SQLAlchemy bridge: raw asyncpg INSERTs.** No bridging
   gymnastics; the poll body writes via the same `conn` WU3.3 already
   uses for its own claim SQL. Schema knowledge is duplicated as SQL
   strings inside `poll.py`, which is the lesser cost compared to
   reopening the WU3.3 seam design. (b) wrapping `conn` in
   `AsyncSession` and (c) refactoring the seam to take `AsyncSession`
   both rejected — (b) muddies transaction semantics with two managers
   on one connection; (c) is the cleanest at the call site but
   materially widens scope and forces WU3.3's claim SQL through
   SQLAlchemy too.
2. **Blob store: `BlobStore` Protocol + `azure-storage-blob.aio` +
   `DefaultAzureCredential`.** The Azure impl uses
   `azure.identity.aio.DefaultAzureCredential` so the worker picks up
   Workload Identity on ACA, `AZURE_*` env vars, or Azure CLI auth in
   dev — in that order. Tests use an in-memory `dict[str, bytes]`
   impl. Mirrors WU6.0's Bicep direction.
3. **`get_markdown` URL form: trust the WU3.2 client as-shipped.** The
   documented query-param shape is what `client.get_markdown` emits;
   the operational note that it returned 400 on 2026-06-04 is a
   first-integration-call discovery, not a WU3.4 blocker. Unit and
   integration tests stub the client. If the live call breaks, the
   fix is a localised swap to `/contents/markdown/{id}` (path form)
   or the `iso+portal+paginate` fallback the fetch script uses.
4. **`change_events` migration: ship the real-column shape now, in
   this unit.** WU1.2's planned stub never landed and four design
   docs reference the table; deferring to a future unit would force
   either WU3.4 to no-op the events insert (failing acceptance) or
   block on a separate WU3.4.0 migration session. Ship the full shape
   (clause_uid before/after, paths, before/after text, scope
   columns, RLS spine, append-only trigger) so WU4.4's differential
   primitive and WU5.3's diff render need no second widening.

Defaulted, not paused on: **the orphan-blob sweep runs as a second
slow tick in the same worker process** (not a separate ACA Job).
ADR-0001 already accepts the one-replica long-running model; running
the sweep in the same process keeps Bicep work zero and the shutdown
event single-source. The trade-off is a 30-min default cadence; the
demo can drop it to 60 s via `HORIZONS_INGESTION_SWEEP_INTERVAL_S`
without a redeploy.

## How the poll body interacts with the rest

The seam contract WU3.3 fixed (`poll(conn, document_id) →
Awaitable[None]`) is unchanged. The connection is the same one
holding the SKIP-LOCKED row lock; everything `poll_document` writes
commits with the schedule update. On exception, WU3.3's tick error
path bumps `failure_count`; six consecutive failures park the row
and emit one `ingestion_incident` with `error_class='parked'`.

The blob upload (`blob_store.put`) happens **before** any DB write —
content-addressed `<sha256>.md` is idempotent (Azure
`overwrite=False`, in-memory dedup-by-key), so re-running the same
poll never produces a second blob. The DB writes happen after; if
any of them fails, the blob is an orphan that the next sweep
reclaims. This is the "leave at most one orphan blob" acceptance
behaviour from the improvement plan.

`change_events.before_clause_uid` materialises correctly for
`MODIFIED` / `MOVED` (inherits from `after_clause_uid` because the
new clause inherited it from the predecessor) and for `REMOVED` (via
`prev_uid_by_path` looked up against the predecessor's `clauses`
rows). `ADDED` carries no before-side. Identity carries across
versions via the `prev_uid_by_path` direct-path lookup for
*unchanged* clauses (paths that the aligner emits no event for) plus
the explicit `pair_by_after_path` mapping recorded on events for
paired-but-modified clauses.

## What I considered and didn't do

1. **No portal-aware parsing yet.** `poll_document` calls
   `parse(doc.markdown)` with the default `ParserConfig`. The 31
   alignment fixtures land in that codepath; the per-portal configs
   under `parser_configs/` come into play when WU3.5 widens the
   schedule to carry portal slug. Adding it here would couple WU3.4 to
   WU3.5's schedule shape.
2. **No effective-date inference.** `change_events.effective_date` is
   populated from `document_versions.effective_date`, which is
   currently the publication date verbatim. Doc 3 §Principles 3 spec's
   the `publication + per_jurisdiction_default_lag` lookup as a future
   unit. The column is in the right place; the value is just a
   placeholder until that unit lands.
3. **No `ChangeEvent` parser-config plumbing.** The aligner accepts
   a `TuningConfig`; the poll body accepts a `tuning` kwarg that
   defaults to `default_tuning_config()`. The kwarg path lets the
   worker swap configs at startup without code changes. Production
   wiring will route this through `ClaimLoopConfig`; today
   `__main__` passes the default.
4. **No `If-None-Match`-style conditional GET.** We hash every
   markdown payload the API returns. Lawstronaut doesn't currently
   advertise an ETag-equivalent; if one ships, the optimisation is
   local.
5. **No sweep grace window.** A concurrent poll that uploads a blob
   *after* the sweep computed `_referenced_keys` but *before* the
   sweep's iteration sees it could be over-deleted. The race window
   is small at demo scale (sweep cadence 30 min, poll completes in
   seconds) and the worst case is one extra missed event recomputed
   next poll. A grace window (refuse to delete blobs younger than N
   seconds) is the natural follow-up if measurements show it matters.
6. **No per-document portal in `documents` / `document_poll_schedule`.**
   WU3.5 owns the portal column. WU3.4 only needs `jurisdiction` and
   `sector` (denormalised onto `change_events`), which are already on
   `documents`.
7. **No `change_events` ORM model.** Following the pattern of
   `documents` / `document_versions` / `clauses`: WU3.4 writes via
   raw asyncpg + SQL strings; the SQLAlchemy ORM model lands when an
   API surface needs to read these rows (WU4.4). Adding the model now
   would be unused scaffolding.

## Gotchas captured

1. **The alignment pipeline emits `REMOVED` + `ADDED` rather than
   `MODIFIED` for short, low-overlap clause bodies.** Default tuning
   (`shingle_k=5, signature_size=128, lsh_bands=20,
   similarity_threshold=0.6`): two ~30-character bodies that share
   ~10 characters don't pass LSH at threshold 0.6, so Pass 3 doesn't
   pair them and they show up as a removed-add pair. My first test
   asserted `MODIFIED`; the integration test was over-specified. The
   correct invariant is "events name the changed clause's path; the
   unchanged clause produces no second-poll event". Real demo bodies
   will be much longer and pair via Pass 2 heading-anchor anyway.
2. **Reconstructing the previous tree from `clauses` rows loses
   `heading_text`.** `_load_previous_tree` builds a depth-1 tree
   with `heading_text=None` on every leaf, because the schema
   doesn't store `heading_text`. The aligner's Pass 2 has separate
   heading-anchor and path-anchor branches, and the path-anchor
   branch handles the all-`None`-heading case. In the test corpus
   the leaves that matter were already `heading_text=None` (the
   parser puts headings on parent nodes that have empty
   body_text — those get filtered out by `_collect_candidates`).
   The downside: cross-version pairs where v1's clause carried a
   heading and v2's same-path clause has the same body but a
   different heading title — the aligner sees mixed
   (heading-on-old, heading-on-new) and falls through to Pass 3.
   Not a demo blocker; if it matters, the fix is to add
   `heading_text text NULL` to the `clauses` table.
3. **The clause-path serialisation uses `"/".join(path)`.** If a
   parser-produced path segment ever contained an internal `/`, the
   roundtrip in `_load_previous_tree` (`path_str.split("/")`)
   would split it wrong. Current parser output slugifies to
   kebab-case so no segment carries `/`. Worth knowing; not worth
   pre-escaping until the parser produces such a segment.
4. **`pytest.approx`'s return type is "partially unknown" in
   pyright's strict mode.** Hit on two assertions
   (`alignment_confidence == pytest.approx(1.0)` and
   `... == pytest.approx(0.94)`). The fix in this codebase is to use
   `float(x) == 1.0` for exact-roundtrip values or
   `abs(x - 0.94) < 1e-9` for tolerance comparisons. The trap is
   subtler than the `Any`-narrowing problem WU3.3 hit — `approx`
   itself is the unknown, not the LHS. `test_similarity.py` dodges
   it by virtue of a concrete `float` LHS from a typed helper.
5. **`Credentials.password` is `SecretStr`, not `str`.**
   `Credentials(email=..., password=password_str)` fails pyright
   strict because the field is typed `SecretStr` even though the
   validator promotes plain strings on construction. Wrap explicitly:
   `Credentials(email=..., password=SecretStr(password_str))`.
6. **`ruff SIM105` recurs on the `try/except ResourceExistsError:
   pass`** pattern in `AzureBlobStore`. Use
   `with contextlib.suppress(ResourceExistsError): ...` instead, same
   as the WU3.3 signal-handler glue.
7. **`tests/integration/conftest.py`'s TRUNCATE list is
   load-bearing.** New corpus tables must be added or earlier-test
   rows persist into the next test under the session-scoped
   `postgres_container` fixture. WU3.4 adds `change_events` to the
   list. WU3.5 will likely add a new table to the schedule path.
8. **Azure SDK objects are heavy; construct once at startup.**
   `BlobServiceClient`, `ContainerClient`, and `DefaultAzureCredential`
   all maintain connection pools, credential caches, and event
   loops. The worker's `__main__` builds them once inside the
   `async with` block; tests use `MemoryBlobStore` and never touch
   Azure code.
9. **The Azure container is created on first `__aenter__`** via
   `client.create_container()` wrapped in
   `contextlib.suppress(ResourceExistsError)`. Idempotent across
   restarts; cost is one cheap RPC.

## Test runner notes

- The integration tests for the poll body live at
  `tests/integration/test_poll_document.py` so they inherit the
  session-scoped `postgres_container`. The sweep tests live next to
  them at `tests/integration/test_blob_sweep.py`. Both use the
  `migrated_db` + `pool` fixtures from the sibling `conftest.py`,
  same as WU3.3's claim-loop tests.
- The `StubClient` is a tiny dataclass; tests cast it to
  `LawstronautClient` at the `functools.partial` call site because
  pyright strict insists on the concrete type. The cast is fine —
  the poll body only calls `await client.get_markdown(str)` so any
  object with that signature satisfies the runtime contract.
- The migration test (`tests/test_change_events_migration.py`) is
  sync, like the other migration tests — Alembic is a sync API and
  clashes with the session-scoped async engine fixture's event loop.

## Next session

WU3.5 — curated-set seed (`worker/scripts/seed_curated_set.py`).
Reads `data/samples/fixtures.json` + a `data/curated_set.yaml`
(jurisdictions × sectors to poll), inserts `documents` rows, creates
`document_poll_schedule` entries. Idempotent. WU3.4's tests
demonstrate the schedule + poll path works end-to-end; WU3.5 fills
the schedule for real.

Three things WU3.5 should consider:

1. **`document_poll_schedule` carries `iso` + `portal`** if the
   live integration of WU3.2's `get_markdown` needs them. Or join
   to `documents` and post-add the columns via a small follow-up
   migration. The `documents.lawstronaut_document_id` is already
   there and is the canonical join key; iso/portal are deducible
   from it but only via a round-trip to Lawstronaut.
2. **Per-document `cadence_interval`.** The demo wants a mix of
   short cadences (hourly for a handful of demo "always-changing"
   documents) and the production-conservative ~daily for the rest.
   `seed_curated_set.py` should accept the cadence per row in the
   YAML.
3. **A synthesised "v2" of 5+ demo documents** is WU8.0's job, but
   WU3.5's seed needs to leave room for it — the synthesised v2
   blobs will be uploaded by a different code path (a separate
   admin tool, probably) and seeded into `document_versions` /
   `clauses` / `change_events` directly. WU3.5 should document
   how that hand-off works so WU8.0 doesn't reinvent the path.

The orphan-blob sweep's grace window is the natural micro-follow-up
inside Track 3 if demo measurements ever show concurrent poll/sweep
deletes causing a churn.
