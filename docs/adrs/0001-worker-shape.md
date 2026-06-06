# ADR 0001 — Ingestion worker shape: long-running asyncio container

- Status: accepted
- Date: 2026-06-05
- Deciders: John
- Spike commit: see `spikes/wu30/` at this ADR's introducing commit
  (removed in a follow-up commit; recoverable from git history)

## Context and problem statement

`docs/RFC-4 services.md` specifies a "stateless worker process, one
container", driven by a per-document schedule table and a
`SELECT … FOR UPDATE SKIP LOCKED` claim pattern. It does *not* specify
whether that container is a **long-running process with an in-process
scheduler** or a **scheduled job (ACA Job / cron) that runs once and
exits**. The substrate choice is invisible from the Postgres side but
shapes WU3.3 (claim loop), WU3.4 (poll transaction), WU6.0 (Bicep
modules), and every subsequent ingestion-touching unit. WU3.0 spikes
both shapes, compares, and picks.

## Decision drivers

Priority order is fixed and load-bearing (project memory:
`project-horizons-design-priorities`):

1. **Local-dev ergonomics.** Bus factor zero — the next maintainer
   must be able to run the worker locally within ~30 seconds, with no
   external scheduler involvement.
2. **Operational complexity.** Fewer moving parts at 3am beats faster
   iteration in the demo period, all else equal.
3. **Cost shape.** Demo is public for 1–2 days then idle. Idle cost
   matters but at the chosen ACA replica size it is sub-dollar/day on
   either substrate.

When the axes disagree, (1) wins.

## Considered options

- **A. Long-running asyncio container** — one replica, perpetual claim
  loop ticking every ~50 ms; SIGTERM handler drains in-flight work;
  liveness probe on `/healthz`.
- **B. Scheduled ACA Job** — orchestrator invokes a run-once process on
  a cron schedule (ACA Jobs minimum granularity: 1 minute); process
  drains the queue and exits 0.

## Decision outcome

**Chosen: A — long-running asyncio container.**

The tie-breaker is local-dev ergonomics. `python -m horizons_ingestion`
will be the canonical local run, ctrl-C clean, no scheduler to
configure. Cost difference at demo scale is negligible. Reaction
latency to a newly-due row is one tick (~50 ms) under A and at least
one cron interval (≥ 60 s) under B — material to the demo's
"watch-this-change-land-in-real-time" framing.

### Consequences

- **WU3.3 (schedule claim loop)** implements substrate A: an
  `asyncio` claim loop with `asyncpg`, batch SKIP-LOCKED claim per
  tick, graceful SIGTERM drain, `/healthz` endpoint over a tiny aiohttp
  surface for ACA's liveness probe.
- **WU6.0 (Bicep)** provisions a `containerApp` resource (not a `job`),
  with `minReplicas=1`, `maxReplicas=1`, an HTTP `/healthz` probe, and
  scale-rules disabled. One always-on replica — the cost we accept for
  reaction latency.
- **Reconnect strategy is now load-bearing.** A run-once Job tolerates
  transient PG failure by exiting and being re-run by the orchestrator;
  the loop has to reconnect in-process. `asyncpg.create_pool`'s built-in
  reconnect plus a `try/except OperationalError` around the loop body
  is sufficient; flag for code review on WU3.3.
- **Idle CPU is non-zero.** Demo-window cost on the smallest ACA SKU is
  acceptable; revisit if the worker stays in production past the demo
  window without a real customer load.

### Confirmation

Implementation in WU3.3 is "compliant" with this ADR when:
the worker is invoked as a single `python -m horizons_ingestion`
entry point; `docker run … horizons-ingestion` stays alive between
batches; SIGTERM drains in flight work before the container exits;
`/healthz` returns 200 while the loop is running.

## Pros and cons of the options

### A. Long-running asyncio container (chosen)

- **Good:** local-dev is `python -m horizons_ingestion` and ctrl-C —
  no scheduler, no `watch`, no Makefile wrapper.
- **Good:** reaction latency to a newly-due row is one tick (~50 ms).
- **Good:** one pool reused for the worker's lifetime; no per-invocation
  warmup cost.
- **Good:** logs stream as one continuous tail — easier to follow than
  per-invocation log groups.
- **Bad:** SIGTERM handler + liveness probe + reconnect-on-pool-error
  path are all required from day one. Spike: ~16 extra LOC over the
  Job shape, plus a `signal` and `pathlib` import.
- **Bad:** non-zero idle CPU. One replica always on.
- **Bad:** crash loops require a sensible restart policy on ACA; cron
  invocation is inherently restart-loop-free.

### B. Scheduled ACA Job

- **Good:** no signal handling, no liveness probe, no in-process
  reconnect. The orchestrator owns lifecycle.
- **Good:** scale-to-zero idle cost outside the cron tick.
- **Good:** crash on tick N has no effect on tick N+1.
- **Bad:** local-dev requires a `watch -n 60 python -m …` wrapper or
  a Makefile target; less ergonomic than `python -m …` and ctrl-C.
  Fails the tie-breaker.
- **Bad:** reaction latency floor is the cron granularity. ACA Jobs:
  ≥ 1 minute. Demo's "real-time amendment detection" story weakens.
- **Bad:** per-invocation pool warmup (~50–100 ms). Negligible at
  steady state, but it adds up if the cron interval is short.

## More information

- Spike code: `spikes/wu30/{fake_schedule,asyncio_loop,aca_job}.py`
  at the introducing commit. Removed in a follow-up commit; git
  history retains the substrates for future re-examination.
- `docs/RFC-4 services.md` ingestion section — substrate-agnostic
  responsibilities and constraints. Unchanged by this ADR.
- Improvement plan: WU3.0 (this unit), WU3.3, WU3.4, WU6.0.
