# 260607 — Deploy: ACA Jobs were silently stuck on old images

*Last revised: 2026-06-07.*
*Path: journal/260607-deploy-job-image-pinning-fix.md.*

## 1. What broke

`/v1/documents/{id}/versions/{label}/clauses` started returning 500 in
the deployed dev env. Root cause:

```
asyncpg.exceptions.UndefinedColumnError: column clauses.heading_text does not exist
```

Migration 0015 (`clauses.heading_text`, added in `91c998f`) had never
been applied to the staging Postgres, despite every deploy since then
reporting "Migration succeeded".

## 2. Why the migration silently no-op'd

`.github/workflows/deploy.yml` has a `Detect infra changes` gate: when
the head commit doesn't touch `infra/` or the workflow file itself, the
`az deployment group create` step is skipped (its only job is
reconciling Azure resource shape; image-only updates flow through
`az containerapp update` further down).

The Bicep template is also what wires the migration ACA Job's image
parameter to `apiImage` (and reseed-corpus to `workerImage`). So when
Bicep is skipped, the **Jobs stay pinned to whichever image tag the
last infra-touching deploy carried** — even though the API + worker
container apps get refreshed via the post-Bicep `az containerapp
update` calls.

In our case:

- API container app: `sha-9328c56c6d9d` (HEAD)
- migrate Job: `sha-17c9d3163206` (commit `17c9d31`, weeks of commits
  ago)
- seed-demo-accounts Job: `sha-ee55d43594ab`
- reseed-corpus Job: `sha-85327aa0b42f`

`alembic upgrade head` inside the stale migrate image only knows about
revisions up to 0014, finds the DB already at 0014, and exits 0. The
workflow's poll loop sees `Succeeded` and shifts traffic to the new
API revision — which then trips on the column the ORM expects.

## 3. Fix

Added a `Bump ACA Job images to current SHA` step in `prepare-infra`
that runs *before* the migration job start, unconditionally. It
`az containerapp job update --image …` all three Jobs to the SHA the
deploy is shipping. The call is idempotent — a no-op when the tag
already matches, so it's cheap on every deploy regardless of whether
Bicep ran.

This belongs in `deploy.yml`, not in Bicep: Bicep is the right owner
for resource *shape*, but image-tag rollout per deploy is a workflow
concern, same as the API + worker `az containerapp update` calls
already live.

## 4. Follow-ups

- The deployed migrate Job is still pinned to `sha-17c9d3163206`
  *right now*. The fix lands when this commit deploys; the bumped
  image + re-run of migrate then applies 0015 and unblocks /clauses.
- Worth thinking about a regression test for "deployed API ORM matches
  deployed alembic head" — could be as simple as the deploy.yml smoke
  test hitting `/v1/documents?limit=1` against the post-deploy stable
  FQDN, since the document list also touches the clauses table when
  it counts versions. Post-demo cleanup.
- The two non-migrate Jobs (seed-demo-accounts, reseed-corpus) had
  the same latent bug. Fortunately their scripts run python directly
  rather than alembic, so the failure mode would surface as a runtime
  exception against a column the deployed schema didn't yet have —
  more obvious than silent migration no-op, but still wrong.
