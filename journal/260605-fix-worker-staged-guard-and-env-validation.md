# 2026-06-05 — Session P: worker-staged guard + env-var validation

*Last revised: 2026-06-06.*
*Path: journal/260605-fix-worker-staged-guard-and-env-validation.md.*

Two surgical fixes from Session M's post-merge follow-up report,
bundled in one session because they are unrelated and small.

## Fix #1 — DEMO-CRITICAL: worker must not claim staged documents

### The bug

WU8.0 stages a synthetic v2 (v1 + v2 ``document_versions`` + clauses +
``change_events``) for five documents so the demo can show change
events without waiting on Lawstronaut to emit a real v2. The staged
document keeps its original ``document_poll_schedule`` row, with
``next_poll_at`` set by the WU3.5 stagger to ``now + offset`` — well
within the demo window.

Once WU6.3 deploys the worker on a schedule, every tick runs:

```sql
SELECT document_id FROM document_poll_schedule
 WHERE next_poll_at <= now() AND failure_count <= $1
 ORDER BY next_poll_at
 FOR UPDATE SKIP LOCKED LIMIT $2
```

The staged document satisfies this predicate. The worker fetches the
real v1 from Lawstronaut, compares it against the staged v2's sha,
sees a mismatch, and inserts a "v3" with the real v1 content. The
staged change events stay in the DB but no longer connect to the live
version, so the demo's headline moment — "look at this clause that
changed" — quietly degrades to nothing.

WU8.0's journal documents the operational rule "pause the worker
during the demo or leave it idle". That rule lives in prose and was
relying on the operator to remember it on the morning of the demo.
Footgun.

### The fix

``stage_synthetic_v2`` now ``UPDATE``s every staged document's
``document_poll_schedule.next_poll_at`` to ``2026-12-31 00:00:00+00``
in the same transaction as the version + clause + change_event
inserts. A rollback un-parks the row; a commit puts it well past the
demo window.

The choice of sentinel — a fixed far-future timestamp rather than
``NULL`` or a "paused" flag — keeps the schema unchanged. The claim
query already filters on ``next_poll_at <= now()``, so any value past
the demo window suffices to keep the row out of every tick during the
1–2 day showcase. ``2026-12-31`` is chosen as a date the demo
emphatically cannot still be running on; if it is, somebody is having
a much worse day than a parked schedule row would cause.

A document staged without its schedule row first seeded (an
unreachable state in practice — ``stage_synthetic_v2`` already warns
and skips if the ``documents`` row is absent, and ``run_seed`` always
inserts both rows together) produces a 0-row ``UPDATE`` that is
harmless. No new branch in the code path.

### Verification

``tests/integration/test_synthetic_v2_staging.py`` (new) covers both
ends of the guard:

1. ``test_stage_synthetic_v2_parks_schedule_far_future`` stages a
   hermetic, hand-authored v1/v2 markdown pair against the
   testcontainers Postgres 18 and asserts that
   ``document_poll_schedule.next_poll_at`` lands past 2026-06-30.
2. ``test_worker_claim_sql_skips_staged_document`` runs the exact
   ``CLAIM_SQL`` (imported from ``horizons_ingestion.loop``) over an
   asyncpg pool and asserts the staged document's UUID is NOT in the
   claimed batch.

The test markdown is written into ``tmp_path`` so it's not coupled to
any fixture quirks under ``data/samples/synthetic_v2/``. (Aside: the
IE-8064194 fixture pair produces clauses that collide on the
``clauses_unique_path_per_version`` constraint when staged against a
real DB — the parser emits multiple leaves at the same path. Out of
scope for Session P; flagging it here as a follow-up. The
``--stage-synthetic-v2`` smoke check noted in WU8.0's journal ran in
``--dry-run`` mode, which never hits the DB.)

### Tear-down counterpart

``scripts/seed_curated_set.py`` has no ``--teardown`` mode. The user
brief said "Add a tear-down counterpart in --teardown mode (if the
script supports it)"; the conditional is the deciding clause. Skipped.

When a teardown path is added in a future unit, it should rewrite
``next_poll_at`` back to ``now() + cadence_interval`` so polling
resumes naturally — the same expression the ``MARK_OK_SQL`` path in
``horizons_ingestion.loop`` uses on a successful poll.

## Fix #5 — TRIVIAL: whitespace-only env vars rejected

### The bug

``packages/horizons-api/scripts/create_demo_accounts.py`` resolves
demo passwords via:

```python
env_value = os.environ.get(account.password_env)
if env_value is not None and env_value != "":
    resolved[account.email] = env_value
```

``" "`` (a single space) satisfies both conditions and gets accepted
as a real password. The account is provisioned with a hashed
whitespace string, login fails as "wrong credentials", and the
operator chases a phantom bug instead of recognising that the env var
was mis-set.

### The fix

The guard now ``.strip()``s the env value before deciding whether it
counts as set. Whitespace-only values (``""``, ``" "``, ``"\t"``,
``"\n"``, ``" \t\n "``) all resolve to the empty string and fall
through to the ``missing`` list — the same path the CLI uses to print
"refusing to provision: the following password env vars are not set"
and abort.

Real passwords with surrounding whitespace (the common copy-paste
typo ``"  hunter2  "``) are stripped of the edges and used. Internal
whitespace is preserved — a password can legitimately contain a space.

### Verification

``tests/test_create_demo_accounts.py`` gains two new tests:

- ``test_whitespace_only_env_var_treated_as_unset`` —
  parametrized over five whitespace patterns; each must land in
  ``missing``.
- ``test_env_var_with_surrounding_whitespace_is_stripped`` —
  ``"  hunter2  "`` becomes ``"hunter2"``; ``"\teu real pw\n"``
  becomes ``"eu real pw"`` (internal whitespace preserved).

## Status

Gate (per the session brief):

- ``uv run ruff check .`` — clean (one auto-fixed import order).
- ``uv run pyright`` — 0 errors (26 pre-existing warnings).
- ``uv run pytest`` — **555 passed, 4 skipped, 1 deselected**.
- ``uv run pre-commit run --all-files`` — clean (ruff-format
  auto-reformatted one file on first pass; second pass is clean).

## Files touched

- ``packages/horizons-ingestion/src/horizons_ingestion/seed.py`` —
  ``stage_synthetic_v2`` schedule-park UPDATE.
- ``packages/horizons-api/scripts/create_demo_accounts.py`` — strip
  env values before deciding "set".
- ``tests/test_create_demo_accounts.py`` — whitespace coverage.
- ``tests/integration/test_synthetic_v2_staging.py`` (new) — worker
  guard regression.

## Follow-ups for a later session

- IE-8064194 synthetic v2 pair currently violates
  ``clauses_unique_path_per_version`` when staged against a real DB.
  The other four pairs may or may not. ``--stage-synthetic-v2`` should
  grow a non-dry-run integration smoke that catches this, and the
  parser (or staging path) should dedupe colliding paths.
- ``seed_curated_set.py`` would benefit from a real ``--teardown``
  mode that un-parks the schedule rows so post-demo development can
  resume without rebuilding the curated set from scratch.
