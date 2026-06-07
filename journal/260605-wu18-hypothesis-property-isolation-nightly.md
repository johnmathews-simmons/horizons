# 2026-06-05 — WU1.8: Hypothesis property test for isolation (nightly)

*Last revised: 2026-06-05.*
*Path: journal/260605-wu18-hypothesis-property-isolation-nightly.md.*

Closes Track 1. The WU1.7 two-client gate is now generalised to a
Hypothesis-driven property test that draws arbitrary clients ×
subscription scopes × write interleavings, and asserts the universal
isolation invariant through the full repository stack. The test is
scheduled in a dedicated nightly GitHub Actions workflow.

## What landed

1. **`tests/isolation/test_property_isolation.py`** (336 lines). One
   async test, `test_isolation_holds_under_arbitrary_writes`, driven by
   `@given(plan=_isolation_plan())`. The composite strategy generates
   `N ∈ [2, 5]` `ClientBlueprint` instances, each with `M ∈ [1, 3]`
   scope tuples drawn from `{UK, EU, US, IE} × {BANKING, INSURANCE,
   ENERGY, TECH}`, plus `[0, 3]` watchlists and `[0, 3]` documents
   tagged with one of that client's own scopes (so writes are
   scope-legal by construction; the assertions are about *reads*). Per
   example the test seeds the function-scoped Postgres with a UUID
   suffix, then for every generated client:

   - Asserts `WatchlistsRepository.list_for` returns only their
     own rows, and that `get_by_id` on someone else's watchlist returns
     `None`.
   - Asserts `DocumentsRepository.list_all` returns only documents
     whose `(jurisdiction, sector)` is in their scopes, and that
     `get_by_id` returns `None` for any out-of-scope document.
   - Asserts the same scope predicate holds transitively through
     `DocumentVersionsRepository.get_by_id` and
     `ClausesRepository.get_by_id` via the FK-walking RLS policies.
   - Positive cases too: every in-scope row (regardless of which
     client wrote it) is visible.

2. **`hypothesis>=6.112` added to `[dependency-groups].dev`** in the
   workspace `pyproject.toml`. Lock file moved.

3. **`nightly` pytest marker registered**, with `addopts` updated to
   `-m 'not nightly'` — default `uv run pytest` skips the property
   test, keeping the day-to-day sweep fast.

4. **`.github/workflows/nightly.yml`**. Cron `0 4 * * *` + manual
   `workflow_dispatch`. Sync via `astral-sh/setup-uv@v6` (pinned to
   `0.9.27`, same as `ci.yml`), then `uv run pytest -m nightly
   --maxfail=1 -v`. Non-gating — branch protection on `main` is
   unchanged.

5. **Doc updates.** `packages/horizons-core/src/horizons_core/db/rls.md`
   "Status by gate" section extended from "end of WU1.7" to "end of
   WU1.8" with the property test's contract. `CLAUDE.md`'s Commands
   section now documents `uv run pytest -m nightly` and notes the
   default exclusion.

## Open questions resolved at the top of the session

Following the engineering-team Phase 3 sentinel discipline, I paused
with `AskUserQuestion` before the first edit and resolved four design
questions — all four landed on the recommended option:

1. **Marker shape.** New `nightly` marker; `addopts` adds `-m 'not
   nightly'`. Explicit semantics matching the plan, vs implicit
   reliance on the `integration` marker.
2. **Hypothesis style.** Composite `@given` strategy + deterministic
   apply-and-assert loop, vs `RuleBasedStateMachine`. Simpler to
   read; upgrade path to stateful is straightforward if false
   positives later turn up.
3. **Nightly Postgres source.** Testcontainers (same as default CI),
   vs a GitHub Actions service container. Keeps test code identical
   to the day suite; 30s startup is negligible for a nightly.
4. **Repo scope.** All four repos (`WatchlistsRepository` + three
   corpus repos) in one property, vs splitting cross-client and
   subscription-scope into separate tests. One property mirrors the
   production reality of a request touching both surfaces.

## Verification

- Default sweep: 86 passed, 1 deselected (the new property test) in
  5.65s. 100% line+branch coverage on tracked Python source — the
  property test exercises the existing repo paths.
- Property test: 25 examples × N-client seeding, passed in 3.78s
  locally against testcontainers Postgres 18.
- Ruff check / format, pyright (strict, 0 errors), pre-commit — all
  clean.
- Nightly workflow validated by `gh workflow run nightly.yml --ref
  main` immediately after the ff-merge. Run 27009724940 completed
  successfully in 39s — `actions/checkout@v4`, `setup-uv@v6` cache
  hit, testcontainers PG18 spinup, 1 test passed.

## Gotchas encountered

1. **Hypothesis health check fired on the function-scoped fixtures.**
   The `migrated_db` + `async_engine` fixtures from
   `tests/isolation/conftest.py` are function-scoped (forced by
   asyncpg's loop-binding issue, see WU1.7's journal). Hypothesis
   normally warns `HealthCheck.function_scoped_fixture` because
   examples within one test share that single fixture invocation. For
   this test that's deliberate — namespacing per example via UUID
   suffix lets every example coexist in one migrated DB without
   collision, and the universal invariant is shape-independent of
   what other rows exist. Suppressed the check explicitly via
   `suppress_health_check=[HealthCheck.function_scoped_fixture]`.

2. **Ruff format ran the first time the new file was written.** Same
   trap as last session's WU1.7 (and the WU1.7 post-merge fix
   commit): the workflow-root `uv run ruff format` is the canonical
   formatter, and `uv run ruff check` does not enforce format. Ran
   `uv run ruff format` explicitly before the commit; pre-commit's
   ruff-format hook stayed clean as a result.

3. **Webapp lint failed locally with `run-s: command not found`.**
   The worktree didn't have `npm install` run against
   `packages/horizons-webapp`. WU1.8 doesn't touch the webapp, so the
   webapp portion of the local sweep is irrelevant for this change.
   The remote `webapp.yml` CI ran on the feature branch push as
   usual and is the actual webapp gate. Noting this so future WUs
   that don't touch the webapp can skip the local webapp lint
   without surprise.

## What's next

Track 1 is now closed. The remaining work units in the plan are:

- **WU1.9 · Admin operator + impersonation paths.** Depends on WU1.6
  (already done), not on WU1.8. Audit-logged admin sessions, bypass
  vs impersonation modes. Tractable next session.
- **Track 2 (alignment).** WU2.0 (clause-tree parser using
  `markdown-it-py`) is the entry point and the first piece of
  domain code that touches the 31 fixtures in `data/samples/`.
- **Track 3 (ingestion).** WU3.0 is the worker-shape ADR spike.
- **Track 4 (FastAPI).** Blocked on at least one corpus surface from
  Track 2 being ready to expose, but the FastAPI scaffolding can
  begin in parallel.

Pick WU1.9 or jump to Track 2 first — the Track-1 isolation contract
is the foundation everything else builds on, and that foundation is
now defended at three layers (Postgres grants, RLS policies,
repository) plus two test layers (two-client gate, N-client
property).

## Cadence note

This is the second session run under the worktree → fast-forward main
→ direct push cadence (the `protect-main` ruleset's required status
checks were dropped on 2026-06-05). It worked smoothly:

1. Local sweep green in the worktree.
2. `git push -u origin worktree-eng-wu1.8-property-test` — triggered
   feature-branch CI (which ran identically to a PR run, just with
   no PR open).
3. `git -C <repo> merge --ff-only worktree-…` from the main
   checkout, `git -C <repo> push origin main`.
4. `git push origin --delete worktree-…` to clean up the now-merged
   remote branch.
5. `ExitWorktree(action="remove", discard_changes=true)` from the
   session to drop the local worktree + branch.

The whole post-commit dance was four commands plus the worktree
exit. No PR friction, no waiting for status checks.
