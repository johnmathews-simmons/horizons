# WU3.3 — Schedule claim loop

*Last revised: 2026-06-06.*
*Path: journal/260605-wu33-claim-loop.md.*

*Session 2026-06-05. Branch `worktree-eng-wu3.3-claim-loop` → ff-merged to `main`.*

The ingestion worker's hot path. First real application code under
`packages/horizons-ingestion/src/horizons_ingestion/` — until now the
package was a `__version__` stub. WU3.3 ships the long-running asyncio
container substrate fixed by [ADR-0001](../docs/adrs/0001-worker-shape.md):
a `SELECT ... FOR UPDATE SKIP LOCKED` claim loop, a kill-switch on
consecutive failures, `/healthz` over a tiny aiohttp surface, and a
SIGTERM-drain. The per-document poll body is stubbed (`noop_poll`); WU3.4
will slot the real Lawstronaut-driven version-write transaction into the
same seam.

## What shipped

1. `packages/horizons-ingestion/src/horizons_ingestion/loop.py` — `ClaimLoop`, `LoopState`, `PoolConnection` / `PollFn` type aliases, `noop_poll`. One tick acquires a pooled connection, opens a transaction, runs the verbatim ADR-0001 SKIP LOCKED SQL, calls `poll(conn, document_id)` for each claimed row, and updates the schedule entry. On exception, increments `failure_count`; when the post-increment count exceeds `failure_threshold` (default 5), writes one `ingestion_incident` row with `error_class='parked'` and a JSON payload (`message`, `error_type`, `failure_count`).
2. `packages/horizons-ingestion/src/horizons_ingestion/config.py` — frozen `ClaimLoopConfig` dataclass plus `asyncpg_dsn()` helper. Every knob (`tick_interval_s`, `batch_size`, `failure_threshold`, `healthz_*`, `pool_min/max`) is overridable via `HORIZONS_INGESTION_*` env vars. Validates `pool_min <= pool_max`. The DSN helper strips SQLAlchemy `+asyncpg` / `+psycopg2` / `+psycopg` prefixes so the testcontainer's SQLAlchemy-shaped URL feeds straight into `asyncpg.create_pool`.
3. `packages/horizons-ingestion/src/horizons_ingestion/health.py` — `LoopHealth` (last-tick timestamp + staleness threshold) and `build_healthz_app()` (an `aiohttp.web.Application` exposing `GET /healthz`). 200 when fresh, 503 (with the stalled age) when stale or pre-first-tick. No DB hit on the probe path — the loop itself exercises the DB every tick and a stalled loop is the failure mode the probe is meant to catch.
4. `packages/horizons-ingestion/src/horizons_ingestion/__main__.py` — `python -m horizons_ingestion` entry point. Builds the pool, the aiohttp `/healthz` site, the `ClaimLoop`, and an `asyncio.Event` shutdown signal wired to SIGTERM/SIGINT via `loop.add_signal_handler`. The four ADR §Confirmation invariants live here.
5. `packages/horizons-ingestion/src/horizons_ingestion/loop.md` — the implementation-level design doc next to the code. Documents the SQL, tick anatomy, pool sizing rationale, liveness semantics, SIGTERM-drain shape, the env-var → field table, and the poll-seam contract.
6. Tests:
   - `tests/integration/test_claim_loop.py` (6 tests, marked `integration`) — drives the loop against a testcontainers Postgres 18 with the full Alembic tree applied. Asserts: noop poll bumps `next_poll_at`+resets `failure_count`; two concurrent `tick()` calls split the due rows via SKIP LOCKED with no double-claim; one failure bumps the counter without an incident; six failures cross the threshold, write exactly one `parked` incident, and the schedule row is skipped on the seventh tick; `run(shutdown)` finishes the in-flight tick before exiting; an idle `run` exits within one tick interval of `shutdown.set()`.
   - `packages/horizons-ingestion/tests/test_config.py` (6 tests) — env defaults, env overrides, `KeyError` without `HORIZONS_DB_URL`, `ValueError` when `pool_min > pool_max`, parametrised DSN-stripping for the three SQLAlchemy prefixes.
   - `packages/horizons-ingestion/tests/test_health.py` (3 tests) — 200 fresh, 503 stale, 503 pre-first-tick via injected clock + `aiohttp.test_utils.TestClient`.
7. `packages/horizons-ingestion/pyproject.toml` — added `aiohttp>=3.10` and `asyncpg>=0.30` to the package's runtime deps (they were dev-only before).
8. `pyproject.toml` (workspace root) — added `asyncpg-stubs>=0.31` to the dev group so pyright's strict mode has a typed surface for asyncpg's `Pool` / `Connection` / `Record` / `PoolConnectionProxy`.

Cumulative diff: 5 new Python modules + 1 design doc + 1 integration-tests subdirectory + 2 unit-test files + 2 `pyproject.toml` edits + lockfile bump. Full sweep green: **333 unit + 4 skipped** (~52 s wall-clock with testcontainer startup amortised over the session); `ruff check`, `ruff format --check`, `pyright` (0 errors / 15 warnings, all `Stub file not found` for `testcontainers.postgres` which is pre-existing), `pre-commit run --all-files`, webapp `lint:check` + `build` + `vitest --run` (3/3 passing).

## Decisions resolved up-front

Four questions pinned via `AskUserQuestion` (with previews) before the first edit. Resolutions:

1. **Single shared asyncpg pool, `min_size=2 max_size=4`.** Considered (b) two separate pools (claim vs poll) for back-pressure isolation and (c) `min=1 max=2` to minimise idle cost. With one ACA replica and a single-digit batch size, the back-pressure case never arises and the bookkeeping isn't worth it. The 2/4 floor matches "claim transaction + one parallel poll body" without thrashing on demand for a third connection.
2. **`/healthz` checks loop liveness only — last-tick recency.** Considered (b) liveness + DB ping and (c) liveness + dependent counters. A DB ping doesn't add signal the loop's own DB writes don't already provide — if Postgres is gone, `tick()` raises, the loop logs the error, and `last_tick_at` doesn't advance: the liveness probe catches it on the next interval. Adding a probe-time DB round-trip increases the probe's tail latency (mTLS handshake on a fresh pool checkout) for zero signal gain. The counters option was overkill for a single endpoint.
3. **Poll seam is a typed alias, not a `Protocol` or ABC.** Considered (b) `Protocol` with `__call__` and (c) ABC with abstract `poll()`. Only one real implementation is planned (WU3.4); a `Protocol` would be the same boilerplate with no test-fixture leverage (lambdas already satisfy the alias). Lift to `Protocol` if a second implementation emerges. The alias is `PollFn = Callable[[PoolConnection, uuid.UUID], Awaitable[None]]`; `PoolConnection` is itself an alias union of `asyncpg.Connection` and `asyncpg.pool.PoolConnectionProxy` because the pool hands out the proxy and the stub types distinguish them.
4. **Tick cadence is configurable, default 50 ms.** Considered (b) hardcoded constant. CLAUDE.md mandates "configuration over code for tuning parameters" specifically so the demo can re-tune without a redeploy; encoding the ADR's 50 ms as a default (not a constant) makes tests trivially fast (`tick_interval_s=0.0`) and leaves demo-time tuning on the table.

## How the poll seam interacts with WU3.4

The seam is `poll(conn, document_id)` where `conn` is the **same** connection that holds the SKIP-LOCKED row lock. Anything `poll` writes through it commits atomically with the schedule update. WU3.4's per-document poll transaction (fetch markdown, hash, write `document_versions` + clauses + alignment + change_events) lands here unchanged in shape: it gets a connection inside an open transaction, does its writes, returns. If the fetch raises, WU3.3's `tick()` catches it and runs the failure path (counter bump + threshold check + maybe-incident).

One trade-off accepted: the lock is held for the entire batch's work, including the real HTTP fetch WU3.4 will do. At one replica + small batch size + sub-second HTTP latencies, this is fine. If we ever need lock-release-before-fetch, the refactor is local — `tick()` becomes "claim batch + bump next_poll_at + commit + then poll" with WU3.4 opening its own short transaction for the version write. WU3.3 deliberately doesn't pre-build that scaffolding.

## What I considered and didn't do

1. **No `docs/RFC-4 services.md` update.** Doc 4 says the worker "does not serve HTTP". ADR-0001 then adds `/healthz` as a substrate-specific carve-out. WU3.0's journal explicitly chose to leave doc 4 substrate-agnostic and let the ADR be the more specific layer; WU3.3 holds the same posture. Adding "except `/healthz` for the liveness probe" to doc 4 conflates the levels.
2. **No `roles.md` update.** The role grants the worker uses are exactly what WU3.1's migration `0007` already granted to `ingestion_worker` (SELECT/INSERT/UPDATE on `document_poll_schedule`, SELECT/INSERT + USAGE on the bigserial sequence for `ingestion_incident`, column-scoped UPDATE on `document_versions.valid_to`). The roles doc is current; no rewrite.
3. **No `SET ROLE ingestion_worker` in the worker connection bracket.** The deployment model is a per-env LOGIN user granted `ingestion_worker` with default `INHERIT`. The user's session inherits the role's grants automatically. Adding `SET ROLE` would shadow that model with code-side coupling and break local-dev (the testcontainer's superuser doesn't need it). The migration tests in `tests/test_ingestion_tables_migration.py` already prove the grants are correct at the schema level; the worker just connects.
4. **No reconnect-on-pool-error path inside `tick()`.** ADR-0001 flags this as a WU3.3 review item; asyncpg's `Pool` already auto-reconnects on transient failures (connection lost between checkouts) and the loop's `except Exception` around `tick()` keeps it alive across hard failures. A dedicated `OperationalError` branch would be premature; if the demo period reveals classes of failure that wedge the pool, it's a localised follow-up.
5. **No two-replica contention test.** SKIP LOCKED's value is multi-replica drain-the-queue-once. ADR-0001 fixes `minReplicas=maxReplicas=1`, so the test would prove a property nothing exercises. WU3.3 *does* test SKIP LOCKED's no-double-claim behaviour with two concurrent `tick()` calls in one process — which is the same lock-acquisition semantics under one event loop — but doesn't spawn a second OS process.

## Gotchas captured

1. **asyncpg has no PEP 561 stubs in the wheel; `asyncpg-stubs` (a community package) supplies them.** Without it, pyright's strict mode floods the worker code with `reportUnknownMemberType` / `reportUnknownVariableType` / `Argument type is unknown` on every `pool.acquire()`, `conn.fetch(...)`, `conn.transaction()`. Added as a dev dep. The same trap will hit WU3.4 the moment it imports asyncpg directly — `asyncpg-stubs` is now the project-wide answer.
2. **`pool.acquire()` returns `PoolConnectionProxy[Record]`, not `Connection[Record]`.** asyncpg-stubs distinguishes the two; both expose the same query surface (`execute`, `fetch`, `fetchval`, `transaction`) but they are not subtypes of each other in the stub hierarchy. The seam needs a union type alias (`PoolConnection`) or the seam-receiver code triggers `reportArgumentType` errors. Documented in `loop.py` next to the alias.
3. **`aiohttp.test_utils.TestClient`'s `async with` context owns the connection; `await resp.text()` after the block raises `ClientConnectionError("Connection closed")`.** First red I hit on the WU3.3 unit tests. Read the body **inside** the `async with`.
4. **`testcontainers.PostgresContainer.get_connection_url()` returns the SQLAlchemy-style `postgresql+<driver>://`.** asyncpg's native `create_pool` rejects the `+driver` prefix. `config.asyncpg_dsn()` strips it; the alternative (`get_connection_url(driver="")`) is fragile across testcontainers versions. The same trap bit the WU3.0 spike — codified as a helper now so WU3.4 inherits it.
5. **WU3.1 tests truncate `change_events` in their per-test reset; `change_events` doesn't exist yet.** Adopted the same per-test `TRUNCATE` pattern in `tests/integration/conftest.py` and hit `relation "change_events" does not exist` immediately. The session-scoped `postgres_container` means rows from earlier tests would persist otherwise — the truncate is load-bearing for deterministic seeds. Dropped `change_events` from the truncate list until a future migration adds it.
6. **Ruff `SIM105` fires on `try/except NotImplementedError: pass`.** `loop.add_signal_handler` raises `NotImplementedError` on Windows/non-Unix loops; the natural shape is the swallow-and-continue. Use `contextlib.suppress(NotImplementedError)` instead (ruff itself suggests it). Worth knowing for any future signal-handler glue.
7. **`from collections.abc import Mapping` for type-only use triggers `TC003`.** Ruff's typing-only-stdlib-import rule mandates moving it under `if TYPE_CHECKING:`. Same trap WU3.0 hit on TC002 for asyncpg. The general lesson: ruff treats any import used *only* in annotations as type-only, even with `from __future__ import annotations`.

## Test runner notes

- `tests/integration/test_claim_loop.py` lives at the cross-package integration root because it inherits the session-scoped `postgres_container` from `tests/conftest.py`. Putting the integration tests under `packages/horizons-ingestion/tests/` would force a duplicate `postgres_container` fixture or a long parent-pointing `conftest`. The cross-package `tests/` root is the established home for integration suites (`tests/isolation/`, `tests/alignment/`).
- `pytest --import-mode=importlib` finds the new `tests/integration/__init__.py` and module without issue. The migration-test pattern (sync engine for Alembic, asyncpg DSN for the worker) is the same shape as `tests/isolation/conftest.py` — borrowed wholesale.

## Next session

WU3.4 — the per-document poll transaction. Slots into the `PollFn` seam designed by WU3.3 with no shape changes. Its responsibilities (per the improvement plan): (a) fetch the markdown via the WU3.2 Lawstronaut client, (b) compute the content hash, (c) extend `valid_to` if unchanged, (d) upload to `originals/<sha256>.md` and open a Postgres transaction wrapping the version row + parsed clauses + alignment output + change events if changed, (e) leave at most one orphan blob on failed runs, reclaimed by a periodic sweep.

Two things WU3.4 should plan around:

- **Lock-hold duration during HTTP.** WU3.3's tick holds the SKIP-LOCKED row lock for the entire batch including the poll body. Lawstronaut's median latency is a few hundred ms; one stuck slow fetch with `batch_size=10` blocks nine other rows for its duration. If WU3.4's measurements show this matters at demo scale, the refactor is local — claim the batch, commit the schedule's `next_poll_at` bump, then do the polls in separate short transactions. Don't pre-build that scaffolding; measure first.
- **The `failure_count` reset on success path.** WU3.3 resets to 0 on every success (consecutive-failures semantics, per the schema doc). WU3.4 inherits this — if the operator wants total failures, the column rename is centralised in `MARK_OK_SQL`.

WU3.2 (Lawstronaut client) doesn't strictly block WU3.4 — WU3.4 can be implemented against a stub client first — but the plan order is WU3.2 → WU3.4, and the client's token-refresh seam will probably want to be in place before the worker code starts caring about transient HTTP errors.
