# WU3.0 — Worker shape spike

*Session 2026-06-05. Branch `worktree-eng-wu3.0-worker-spike` → ff-merged to `main`.*

Opening unit of Track 3 (Ingestion worker). First work unit in the repo whose deliverable is a *decision*, not a feature — `docs/adrs/0001-worker-shape.md` (MADR v4) picks between two Azure-Container-Apps-shaped substrates for the ingestion worker: a long-running asyncio container vs. a scheduled ACA Job. WU3.3 / WU3.4 / WU6.0 inherit the choice; getting it right here is cheaper than relitigating it later.

Also lands `docs/adrs/README.md` and the `docs/adrs/` directory itself, anchoring the ADR convention for the units to come.

## What shipped

1. `docs/adrs/0001-worker-shape.md` (123 lines) — MADR v4. Status: accepted. Picks the long-running asyncio container with a 50 ms claim tick, SIGTERM-drain, `/healthz` over a tiny aiohttp surface, and `minReplicas=maxReplicas=1` on the eventual ACA `containerApp` resource. Tie-breaker stated up front: local-dev ergonomics wins when the three axes (local-dev, ops complexity, cost shape) disagree. *Confirmation* section names the four observable criteria WU3.3's implementation has to meet to be ADR-compliant.
2. `docs/adrs/README.md` (56 lines) — index + template + lifecycle convention. Establishes MADR v4 as the repo's ADR template, four-digit numbering, and the in-place `Status:` header as the source of truth (no separate state file). Cites `docs/0. about-these-docs.md` §"Architecture Decision Records (ADRs) (the secondary frame)" as the upstream sanction for the practice.
3. Spike code at `spikes/wu30/{fake_schedule,asyncio_loop,aca_job}.py` + `README.md` (~225 LOC, including a shared fake-schedule module and per-substrate run-once scripts). Landed in commit `dd937cd`, removed in commit `348400a` — both on `main`. Git history retains the evidence the ADR cites; the working tree stays lean. The throwaway tag was the explicit choice on Q1 (see *Decisions resolved up-front*).

Cumulative-since-prior-session diff (what `main` actually carries): two new doc files, no code, no test changes. Full sweep green: 221 unit tests + 4 skipped + 85 deselected (44 s wall-clock); `ruff check`, `ruff format --check`, `pyright` (0 errors / 13 stub warnings), `pre-commit run --all-files`, webapp `lint:check` + `build` + `vitest --run` (3/3 passing).

## Decisions resolved up-front

Four questions pinned via `AskUserQuestion` (with previews) before the first edit. Resolutions:

1. **Spike lives at `spikes/wu30/` and is deleted in a second commit on the same branch.** Considered (b) keeping it in `packages/horizons-ingestion/_spike/` (kept the code self-documenting but bloated the package WU3.3 will rewrite) and (c) inline fenced blocks inside the ADR (simpler but the ADR would claim things from never-run code). Throwaway-with-history matches the "history keeps the evidence, tree stays lean" trade and avoids dishonesty in the ADR.
2. **Backing store is testcontainers Postgres 18.** Considered (b) SQLite — has row locking but no `FOR UPDATE SKIP LOCKED`, which is the entire point of the comparison — and (c) an in-memory `dict[int, datetime]` — purely a toy substrate that erases the contention axis. testcontainers costs ~2 s of startup per run but is the only substrate that reproduces the exact SQL WU3.3 will ship.
3. **MADR v4 is the project's ADR template.** Considered (b) Michael Nygard classic (shortest, weaker for multi-option choices) and (c) Y-statement (one sentence, loses the alternatives narrative). MADR v4's *Considered options* + *Pros and cons* structure forces an honest comparison and gives subsequent ADRs (WU3.1 schema, WU6.x infra) a load-bearing template.
4. **Tie-breaker = local-dev ergonomics wins.** Considered (b) ops complexity (favours Jobs), (c) cost shape (favours Jobs), and (d) no fixed tie-breaker (honest but kicks the can). The repo's `project-horizons-design-priorities` memory pins flexibility > visibility > easy-to-understand and the WU2.x sessions consistently rewarded bus-factor-zero ergonomics; encoding it explicitly in the ADR text means WU6.0 doesn't relitigate.

## Spike: what got measured

Both substrates seed 100 due rows into `_spike_schedule(id, next_poll_at, last_polled_at, failure_count)` and drain via:

```sql
SELECT id FROM _spike_schedule
 WHERE next_poll_at <= now()
 ORDER BY next_poll_at
 FOR UPDATE SKIP LOCKED
 LIMIT $1
```

— the exact pattern WU3.3 will ship verbatim. Per-row processing is a no-op (`asyncio.sleep(0)`); WU3.2's Lawstronaut client is out of scope.

Observations (wall-clock numbers are noisy; the comparison is structural):

| axis | asyncio loop | ACA Job |
|---|---|---|
| code LOC (excl. shared `fake_schedule`) | ~71 | ~55 |
| extra stdlib imports vs. Job | `signal`, `pathlib` | — |
| signal handling | required (SIGTERM drain) | not needed |
| liveness probe | required (`/healthz` for ACA probes) | not needed |
| idle CPU | one replica always on | zero between invocations |
| reaction latency to a newly-due row | ~50 ms (tick) | ≥ 60 s (ACA cron minimum) |
| local dev | `python -m horizons_ingestion`, ctrl-C | needs `watch` / Makefile wrapper |
| pool warmup | once per worker lifetime | once per invocation (~50–100 ms) |

The Job shape wins on operational simplicity and idle cost. The asyncio shape wins on reaction latency, local-dev ergonomics, and pool reuse. Under the Q4 tie-breaker, asyncio carries.

## What I considered and didn't do

1. **No services.md update.** `docs/4. services.md` already names "stateless worker process, one container, horizontally scalable if needed" and the `SELECT ... FOR UPDATE SKIP LOCKED` pattern. The ADR sits *under* it — chapter-grained doc 4 stays substrate-agnostic; the ADR is the more specific layer. Updating doc 4 to mention asyncio specifically would conflate the levels. Skipped.
2. **No /healthz prototype in the spike.** A real `/healthz` would need aiohttp (or starlette, or stdlib `http.server`) bolted in. Out of scope for a decision-doc spike; flagged as a WU3.3 requirement in the ADR's *Consequences* and *Confirmation* sections.
3. **No reconnect-on-pool-error code in the spike.** asyncpg's pool reconnects on transient PG failure, but the surrounding loop has to tolerate `OperationalError` from a poisoned connection. Sketched in *Consequences* as a WU3.3 code-review flag rather than spiked here — the substrate choice doesn't depend on it.
4. **No multi-replica contention test.** SKIP LOCKED's value is that multiple worker replicas can drain the same queue without re-claiming rows; the spike never actually spawned a second replica. The ADR doesn't need that evidence — `minReplicas=maxReplicas=1` for the demo means we're not exercising multi-replica anyway. WU3.3 can add a two-replica contention test when (or if) horizontal scaling becomes a concern.
5. **No pyright include for `spikes/`.** Ruff covers the spike (clean under `E F W I UP B SIM TC` after one TC002 fix); pyright's `include` list only names `packages/*/src` and `tests/`. Adding `spikes/` to the include and then dropping it in the same series would be churn. Ruff alone met the bar.

## Plan drift — minor

One ruff TC002 (`typing-only-third-party-import`) on `fake_schedule.py`'s `asyncpg` and `testcontainers.postgres.PostgresContainer` imports — both used only as parameter annotations on functions that never instantiate either. Fix was the standard `if TYPE_CHECKING:` block (`from __future__ import annotations` was already there). Same pattern that bit WU2.4. Worth noting in case a future spike hits it: ruff's TC rules treat ANY annotation-only import this way, even when the runtime call (`pg.get_connection_url()`) looks like it uses the type. The call uses the *value*, not the class — the class itself only appears in the annotation.

## Gotchas captured

1. **testcontainers `PostgresContainer.get_connection_url()` returns the SQLAlchemy-style URL `postgresql+psycopg2://...`** even when you don't pass `driver=`. asyncpg wants plain `postgresql://`. Strip `+psycopg2` (or call with `driver="asyncpg"` and strip `+asyncpg`). The `container_dsn()` helper in the spike does the former.
2. **The worktree's branch name was `worktree-eng-wu3.0-worker-spike`, not `eng-wu3.0-worker-spike`.** `EnterWorktree(name=…)` prepends `worktree-` to whatever you pass. CLAUDE.md's "CI / merge cadence" example shows the bare `eng-…` form; the actual created branch carries the prefix. Harmless — `git push -u origin <branch>` and `git merge --ff-only <branch>` both work fine — but worth knowing so the push command matches the actual branch name.
3. **Pre-commit hooks run from `.git/hooks` inside the *main* checkout's hooks dir, not the worktree's.** `uv run pre-commit run --all-files` ran the full sweep against the worktree's working tree without issue, but `git commit` from inside the worktree didn't appear to trigger the configured hooks (no failure either — the local sweep had already verified everything). For a unit that touches code subject to per-file hook gates, run `pre-commit run --all-files` explicitly before `git commit` rather than relying on commit-time triggering inside a worktree.
4. **Webapp deps don't carry across worktree creation.** The worktree starts with no `node_modules/`; running `npm run lint:check` from a fresh worktree errors with `run-s: command not found` because `npm-run-all` (the `run-s` provider) isn't installed yet. `npm ci` first. Cost: ~10–15 s. Not a blocker, but the local sweep has to include this step in any worktree where the webapp gate matters.

## Output of the spike runs

```
$ uv run python -m spikes.wu30.asyncio_loop
seeded=100 remaining_due_before=100
processed=100 remaining_due_after=0
( wall-clock 3.5s incl. ~2s testcontainer startup )

$ uv run python -m spikes.wu30.aca_job
seeded=100 remaining_due_before=100
processed=100 remaining_due_after=0
( wall-clock 1.8s incl. ~1s testcontainer startup, image cached )
```

Both substrates drain the seeded queue cleanly. The wall-clock delta is dominated by testcontainer startup, not by the substrates themselves — the comparison rests on the structural axes in the table above, not on these numbers.

## Next session

The chosen substrate (long-running asyncio container) is now load-bearing for every downstream Track-3 unit. The next unit per the improvement plan is **WU3.1** — the real `documents` / `document_versions` / `document_poll_schedule` / `ingestion_incident` schema as an Alembic migration. WU3.1 doesn't depend on the ADR text directly (the schema is substrate-agnostic) but the `document_poll_schedule` row shape needs to match what WU3.3's claim loop will read — the spike's `_spike_schedule(id, next_poll_at, last_polled_at, failure_count)` is a faithful preview but the real table will add indexing per access pattern and the `ingestion_worker` PG role's grants.

Two follow-ups that are not WU3.1's problem but should be on someone's radar:

- **`/healthz` over aiohttp belongs to WU3.3, not WU3.1.** When WU3.3 lands, the smallest aiohttp setup that ACA's liveness probe can hit is the right move — full FastAPI would be overkill for a single endpoint that returns 200.
- **Spike code is now in git history at commit `dd937cd`.** Anyone needing to re-examine the substrate comparison can `git show dd937cd:spikes/wu30/asyncio_loop.py` etc. The ADR cites this commit by hash in its *More information* section.
