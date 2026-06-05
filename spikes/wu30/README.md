# WU3.0 worker-shape spike

Throwaway. Decision-support code for `docs/adrs/0001-worker-shape.md`.
Deleted in a follow-up commit once the ADR has captured what the runs
showed.

Two candidate substrates against the same fake schedule table:

- `asyncio_loop.py` — long-running asyncio container; perpetual claim
  loop ticking every 50 ms; SIGTERM-aware; touches a liveness file
  every tick.
- `aca_job.py` — run-once: drain the queue, print a report, exit 0;
  expects an external orchestrator (ACA Job / cron) to invoke it.

Both share `fake_schedule.py`: `_spike_schedule(id, next_poll_at,
last_polled_at, failure_count)` plus the
`SELECT ... FOR UPDATE SKIP LOCKED LIMIT N` SQL the real `WU3.3` claim
loop will reuse verbatim. Backing store is testcontainers Postgres 18
(`postgres:18-alpine`) — the only substrate that reproduces SKIP LOCKED
faithfully.

## Run

```bash
# Docker has to be reachable. ~5–10s startup for the PG container.
uv run python -m spikes.wu30.asyncio_loop
uv run python -m spikes.wu30.aca_job
```

Both seed 100 rows due-right-now, drain them, and print:

```
seeded=100 remaining_due_before=100
processed=100 remaining_due_after=0
```

## What the comparison was actually for

Not benchmarks — both substrates drain 100 rows in ~the same wall-clock
(dominated by PG roundtrips inside the testcontainer, not by the
substrate). The interesting axes are:

| axis | asyncio loop | ACA Job |
|---|---|---|
| LOC for the loop itself | ~30 | ~15 |
| signal handling | required (SIGTERM drain) | not needed |
| liveness probe | required (`/healthz` for ACA) | not needed |
| idle CPU | one replica always on | zero between invocations |
| reaction latency to a newly-due row | one tick (~50 ms) | up to one cron interval (≥ 1 min on ACA) |
| local dev | `python -m …` once, ctrl-c to stop | needs a `watch` or invoke-on-demand wrapper |
| connection pool reuse | one pool for the worker's lifetime | new pool per invocation (~50–100 ms warm-up) |

The ADR weighs these on the project's stated tie-breaker (`local-dev
ergonomics wins`) and picks accordingly. See
`docs/adrs/0001-worker-shape.md`.
