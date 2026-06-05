<!--
PR template for horizons. Delete sections that don't apply; keep the
Migrations checklist whenever the PR touches anything under
`packages/horizons-core/migrations/` or any RLS policy.
-->

## Summary

<!-- One or two sentences: what this PR changes and why. The "why" is
the half a future reader actually needs. -->

## Migrations checklist

*Required for any PR that adds or modifies files under
`packages/horizons-core/migrations/` or touches an RLS policy. Delete
this section if it doesn't apply.*

See `docs/runbooks/migrations.md` for the rules behind the
checks below.

- [ ] **Expand-contract safety:** Is every change in this migration
  safe across two consecutive deploys? The deploy pipeline applies
  the migration *before* shifting traffic to the new revision, so the
  previous revision's code briefly runs against the new schema. If a
  contract step (DROP COLUMN, DROP TABLE, NOT NULL promotion) is
  included, has it been split into a separate migration shipped in a
  later deploy?
- [ ] **RLS policy ordering:** Does any RLS policy in this PR tighten
  or loosen reads? Tightening ships **one deploy before** the code
  that depends on it; loosening ships **one deploy after**. Confirm
  the ordering — or that this is an add-only policy on a new
  column, which is exempt.
- [ ] **Backward compatibility:** Will the previous revision's code
  still serve traffic correctly while this migration is applied? If
  not, split the migration.
- [ ] **Idempotent / re-runnable:** Each statement is safe to retry
  from any partial state (the migration ACA Job runs with
  `replicaRetryLimit: 0`, so a partial failure leaves the DB in
  whatever shape the failing statement left it; the next deploy
  attempt re-runs `alembic upgrade head`).

## Test plan

<!-- Bulleted checklist of how the change was verified. Local sweep
(`uv run pytest -m "not integration" && uv run ruff check . && uv run
pyright && uv run pre-commit run --all-files`), any integration
tests run, any manual UI / API smoke. -->
