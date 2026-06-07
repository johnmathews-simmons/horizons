# Claim loop

*Last revised: 2026-06-05.*
*Path: packages/horizons-ingestion/src/horizons_ingestion/loop.md.*

The ingestion worker's top-level shape, implementing the long-running
asyncio container substrate fixed by
[ADR-0001](../../../../docs/adrs/0001-worker-shape.md).

The loop reads `document_poll_schedule` (WU3.1) via
`SELECT ... FOR UPDATE SKIP LOCKED` and calls a pluggable
`PollFn` for each claimed row. WU3.3 ships the loop with a no-op poll;
[WU3.4](../../../../README.md) slots its per-document poll transaction
into the same seam.

## SQL

The claim query is fixed:

```sql
SELECT document_id FROM document_poll_schedule
 WHERE next_poll_at <= now() AND failure_count <= $1
 ORDER BY next_poll_at
 FOR UPDATE SKIP LOCKED LIMIT $2
```

Bound to `(failure_threshold, batch_size)` from `ClaimLoopConfig`. The
matching index `idx_document_poll_schedule_next_poll_at` was added by
migration `0007_ingestion_tables`.

## Tick anatomy

One tick acquires a pooled connection, opens a transaction, claims a
batch, and processes each claimed row inside the same transaction:

1. `BEGIN`.
2. `SELECT ... FOR UPDATE SKIP LOCKED LIMIT N` — N row locks held until
   `COMMIT`. Other replicas (today there is only one — see
   [`ADR-0001`](../../../../docs/adrs/0001-worker-shape.md)) skip locked
   rows.
3. For each `document_id`:
   a. Call `poll(conn, document_id)`. The connection is the same one
      that holds the lock; WU3.4's poll body will issue its
      `document_versions` / `clauses` / `change_events` writes through
      it and they commit atomically with the schedule update.
   b. On success — `UPDATE document_poll_schedule
        SET last_polled_at = now(),
            next_poll_at = now() + cadence_interval,
            failure_count = 0
        WHERE document_id = $1`.
   c. On exception — `UPDATE document_poll_schedule
        SET last_polled_at = now(),
            failure_count = failure_count + 1
        WHERE document_id = $1 RETURNING failure_count`.
      If the returned `failure_count` exceeds the threshold, write an
      `ingestion_incident` row with `error_class = 'parked'` and the
      exception text in `payload`. The schedule entry stays put; the
      next-tick claim query filters it out via `failure_count <= 5`.
4. `COMMIT`.
5. Stamp `last_tick_at = now()`. The liveness probe (`health.py`) reads
   this.
6. `await asyncio.sleep(tick_interval_s)` — default 50 ms, per
   [`ADR-0001`](../../../../docs/adrs/0001-worker-shape.md).

## Connection pool

One shared `asyncpg.Pool` per worker, `min_size=2 max_size=4`. Used for
both the claim transaction and any side queries WU3.4 layers on. ACA
runs one replica today (`minReplicas=maxReplicas=1`), so two warm
connections at idle is the sizing floor.

## Liveness

`/healthz` over a tiny `aiohttp.web.Application` returns 200 iff the
loop's `last_tick_at` is within `healthz_stale_after_s` (default
`5.0`). Returns 503 otherwise with the stalled age in the body. No DB
hit on the probe path — the loop itself exercises the DB every tick,
and a stalled loop is the failure mode the probe is meant to catch.

## SIGTERM-drain

The entrypoint (`__main__.py`) installs `SIGTERM` and `SIGINT`
handlers that set an `asyncio.Event`. `ClaimLoop.run(shutdown)`
checks the event between ticks. The in-flight tick is allowed to
complete — its `COMMIT` writes the schedule update — and then the
loop exits cleanly. New claims do not start once the event is set.

## Configuration

`ClaimLoopConfig` is a frozen dataclass loaded from environment
variables via `from_env`. Defaults are the ADR-stated values; every
knob is overridable so the demo can re-tune live without a redeploy
(see CLAUDE.md §"Configuration over code"):

| env var | field | default |
| --- | --- | --- |
| `HORIZONS_DB_URL` | `db_url` | (required) |
| `HORIZONS_INGESTION_TICK_INTERVAL_S` | `tick_interval_s` | `0.05` |
| `HORIZONS_INGESTION_BATCH_SIZE` | `batch_size` | `10` |
| `HORIZONS_INGESTION_FAILURE_THRESHOLD` | `failure_threshold` | `5` |
| `HORIZONS_INGESTION_HEALTHZ_STALE_AFTER_S` | `healthz_stale_after_s` | `5.0` |
| `HORIZONS_INGESTION_HEALTHZ_HOST` | `healthz_host` | `0.0.0.0` |
| `HORIZONS_INGESTION_HEALTHZ_PORT` | `healthz_port` | `8080` |
| `HORIZONS_INGESTION_POOL_MIN` | `pool_min` | `2` |
| `HORIZONS_INGESTION_POOL_MAX` | `pool_max` | `4` |
| `HORIZONS_INGESTION_BLOB_ACCOUNT_URL` | `blob_account_url` | (required for deploy) |
| `HORIZONS_INGESTION_BLOB_CONTAINER` | `blob_container` | `originals` |
| `HORIZONS_INGESTION_SWEEP_INTERVAL_S` | `sweep_interval_s` | `1800.0` |

The asyncpg DSN the worker uses is a plain `postgresql://` URL — not
the SQLAlchemy-style `postgresql+asyncpg://` the API uses. The
testcontainer's `get_connection_url(driver="asyncpg")` returns the
SQLAlchemy form; `config.asyncpg_dsn()` strips `+asyncpg` if present.

## The poll seam

```python
PollFn = Callable[[asyncpg.Connection, uuid.UUID], Awaitable[None]]
```

An async callable. The connection is the one that holds the claim
lock — anything `poll` writes through it commits with the schedule
update. WU3.3 ships `noop_poll`; WU3.4 ships
[`poll_document`](poll.md) — the real per-document body — which the
worker's `__main__` binds with its `LawstronautClient` and
`BlobStore` via `functools.partial` before handing it to
`ClaimLoop`.

The seam is a type alias (not a `Protocol`, not an ABC) because we
expect exactly one real implementation. If a second implementation
emerges, lift to `Protocol` then.

## The sweep loop

A second long-running coroutine, [`SweepLoop`](sweep.py), runs
concurrently with `ClaimLoop` in `__main__.py`. It iterates every
`sweep_interval_s` (default 30 min): list every blob in
`HORIZONS_INGESTION_BLOB_CONTAINER`, compare against
`document_versions.content_blob_key`, delete unreferenced keys. Same
process, same shutdown event — SIGTERM drains both loops together.
The sweep is the orphan reclaimer WU3.4 acceptance requires.
