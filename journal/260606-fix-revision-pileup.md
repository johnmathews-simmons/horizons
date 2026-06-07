# 2026-06-06 — Fix ACA revision pileup that ate Postgres connection slots

*Last revised: 2026-06-06.*
*Path: journal/260606-fix-revision-pileup.md.*

## What broke

An hour before the 2026-06-08 demo, the reseed-corpus ACA Job failed with:

```
FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute
```

Diagnosis: `horizons-dev-worker` and `horizons-dev-api` each had ~25 active revisions, each running 1 replica with a SQLAlchemy connection pool open against the Flexible Server. Non-superuser slots were exhausted. Manually deactivated everything except the latest revision on each app to unblock the demo:

```bash
KEEP=horizons-dev-worker--sha-<latest>
az containerapp revision list --name horizons-dev-worker -g horizons-nonprod \
  --query "[?properties.active && name != '$KEEP'].name" -o tsv | \
  while read r; do az containerapp revision deactivate --name horizons-dev-worker -g horizons-nonprod --revision "$r"; done
# same for horizons-dev-api
```

That's a one-shot fix. Every future deploy adds another active revision and we drift back toward the same wall.

## Root cause

Both container apps were declared with `activeRevisionsMode: Multiple`:

- The **API** has a real reason to be Multiple: WU6.3's blue/green dance in `deploy.yml` needs NEW at 100 % and PREV at 0 % both active so a manual rollback is a single weight flip. But Multiple mode never auto-deactivates anything, and the workflow never deactivated stale revisions after the traffic shift, so each deploy left another active revision behind.
- The **worker** was Multiple by accident — copied the API's convention without the API's reason. The worker has no traffic to shift (`ADR-0001`: one always-on replica, internal-only ingress for the liveness probe). The misleading inline comment in `deploy.yml` even claimed "ACA promotes the new revision once it's healthy and deactivates the old", which is **Single**-mode behaviour. Multiple did nothing for the worker except let the pile grow.

## Fix — hybrid

Two options were on the table:

1. Switch both apps to Single. Simpler/declarative; ACA auto-deactivates the previous revision on each update.
2. Stay Multiple, add an explicit cleanup step at the end of `deploy.yml`.

Picked a hybrid:

- **Worker → Single.** `infra/modules/container-app-worker.bicep`. The `traffic[]` block is removed because Single mode forbids it. The rationale comment explains why Single is the natural fit and references this journal entry. No deploy.yml changes for the worker — the existing `az containerapp update --revision-suffix sha-X` step automatically deactivates the previous revision under Single mode.
- **API → Multiple + explicit cleanup.** `infra/modules/container-app-api.bicep` is unchanged; the inline rationale already documents Multiple-mode as load-bearing for blue/green. A new step `Deactivate stale API revisions` at the end of `deploy-services` in `.github/workflows/deploy.yml` lists active revisions and deactivates everything that isn't NEW (current 100 %) or PREV (kept at 0 % for rollback per the runbook).

### Why not Single for the API too?

Single mode would auto-deactivate PREV the moment NEW is created. That destroys the rollback affordance the runbook depends on (`docs/runbooks/deploy.md` § Manual rollback — flip weights back to the previous revision). The blue/green sequence in `deploy.yml` (capture PREV → pin PREV → create NEW at 0 % → smoke-test NEW → shift NEW=100 PREV=0) only works in Multiple mode.

### Cleanup-step semantics

- Gated on `steps.shift.conclusion == 'success'`. If the deploy was rolled back to PREV, we don't touch anything — the existing failure path already restored the pre-deploy state and we don't want to incorrectly treat PREV as the "current" survivor.
- Filters with `active && name != '$NEW' && name != '$PREV'`. On bootstrap (PREV empty), `name != ''` excludes nothing real and the filter still does the right thing. On re-run of the same SHA (NEW == PREV), nothing stale exists to deactivate.
- Iterates with `while IFS= read -r r; do ... done <<< "$STALE"` rather than `xargs` — keeps each `az` call in the same process and surfaces failures in the workflow log.

## What I deliberately did NOT do

- **Cleanup of historically inactive revisions.** Inactive revisions don't consume DB slots — they're just named resources. The 25+ inactive worker revisions from before the manual fix can stay; ACA prunes them on its own schedule and they cost nothing.
- **Touch the reseed pipeline.** `scripts/reseed_aca.sh`, `scripts/reseed_corpus.py`, and `infra/modules/reseed-corpus-job.bicep` are working as of 5f1d473. Out of scope for this fix.
- **Touch the API's Bicep rationale.** Multiple mode is correct for the API; the existing comment in `container-app-api.bicep` already explains why. Adding more would be noise.

## Merge gate — post-demo

The demo is 2026-06-08. Pushing this to `main` triggers `build-and-push.yml` → `deploy.yml`, which rolls a fresh deploy through the running staging. Out of scope per the task brief: don't risk destabilising the showcase.

Plan:

1. The fix lives on branch `worktree-fix-revision-pileup` (worktree at `.claude/worktrees/fix-revision-pileup/`).
2. Push the branch (not main) so feature-branch CI runs and we get an early signal that the YAML / Bicep changes are syntactically clean.
3. After the demo (safe window: 2026-06-10 onward), `git -C ../.. merge --ff-only worktree-fix-revision-pileup && git push origin main`.
4. The next deploy after merge:
   - Bicep deploy fires (worker bicep changed) and flips the worker container app to `activeRevisionsMode: Single`.
   - The worker `az containerapp update` step creates a new revision; ACA auto-deactivates the previous worker revision (the one currently serving traffic post-manual-fix). From this point forward, the worker will only ever have one active revision.
   - The API blue/green dance runs as before; the new cleanup step at the end deactivates any stale active API revisions. On the very first run post-merge, this is a no-op (the manual cleanup already left only the latest revision active) — but every subsequent deploy will trim PREV-minus-one and earlier from the active list.

## Files changed

- `infra/modules/container-app-worker.bicep` — `Multiple` → `Single`, drop `traffic[]`, rewrite rationale.
- `.github/workflows/deploy.yml` — rewrite the "Update worker revision" comment, add the "Deactivate stale API revisions" step.
- `docs/runbooks/deploy.md` — document step 7 of the blue/green sequence and the worker's Single-mode behaviour.
- `journal/260606-fix-revision-pileup.md` — this entry.

## Verification

- `az bicep build --file infra/main.bicep` exits 0 — the worker module change compiles.
- Local sweep: `uv run ruff check .`, `uv run pyright`, `uv run pytest`, `uv run pre-commit run --all-files`. No Python source touched, so these are expected no-ops; running them anyway to catch any pre-commit drift (e.g. trailing-whitespace on the YAML / markdown edits).
