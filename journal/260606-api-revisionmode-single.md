# 2026-06-06 — flip the API to `activeRevisionsMode: Single`

*Last revised: 2026-06-06.*
*Path: journal/260606-api-revisionmode-single.md.*

Earlier today's [`260606-fix-revision-pileup.md`](./260606-fix-revision-pileup.md) walked the worker to Single and kept the API in Multiple, adding a `Deactivate stale API revisions` step to the end of `deploy-services` so the pile of zero-weight revisions stops growing. That decision was correct given the immediate goal: stop the reseed Job from running out of Postgres connection slots an hour before the demo.

Re-reading the change-set after the deploy went green, the API-Multiple side of it doesn't pay for itself. This entry captures what we changed and why.

## What changed

- `infra/modules/container-app-api.bicep` — `activeRevisionsMode: 'Multiple'` → `'Single'`.
- `.github/workflows/deploy.yml` — `deploy-services` collapses from 8 steps to 4. Removed: PREV capture, traffic pin, per-revision-FQDN smoke, explicit traffic shift, on-failure traffic rollback, stale-revision cleanup loop. The remaining 4: Azure login, `az containerapp update --api`, stable-FQDN tripwire smoke, `az containerapp update --worker`. Net `-146` lines.
- `docs/runbooks/deploy.md` — `deploy-services (API blue/green)` section rewritten as `deploy-services (API)`. Manual rollback section rewritten as "redeploy the previous SHA". Healthy-run table updated.

## Why now, not at the next deploy

The Multiple-mode API was load-bearing for **instant rollback via traffic-weight flip** (~5s) and for a **pre-traffic smoke gate** against the new revision's unique FQDN. Both of those are real properties, but neither is paying its rent here.

**Instant rollback.** The replacement — `az containerapp update --image :sha-PREV` — takes 3-5 min. The delta is real (~3-5 min vs. ~5s). At demo scale the operator workflow on a regression is "alert fires → 30-60s of 'is this real?' investigation → trigger the rollback", and the regression is a couple-minute incident regardless of whether the rollback machinery takes 5s or 5 min. The 5s number is the upper bound of an idealised case where the operator knew which revision was bad the moment the alert fired. We are not in that case.

**Pre-traffic smoke gate.** The smoke test the previous shape ran was a single curl of `/healthz` + `/openapi.json`. Both endpoints are also targets of the readiness probe ACA already runs every 5s with `failureThreshold: 3` (in `infra/modules/container-app-api.bicep` L194–203). If a revision fails the smoke step's `/healthz`, it would also fail the readiness probe, ACA would not shift traffic, and the previous revision would continue to serve. The smoke gate was, in practice, redundant with the readiness probe.

The Multiple-mode setup is therefore paying for a ~3-5-minute improvement in rollback wall-clock and a redundant smoke gate, in exchange for:

- 6 imperative `az containerapp` steps in `deploy.yml` (~140 lines)
- A "deactivate stale revisions" loop that *also* exists because of Multiple mode
- The reseed-Job-connection-slot failure mode that bit us this morning
- Several pages of `docs/runbooks/deploy.md` describing the dance

Not a great trade at our scale.

## What we kept

The worker stays in Single (no change to `container-app-worker.bicep` beyond a comment trim — the contrast against "the API's blue/green dance" was the only thing that dated). The `--revision-suffix sha-<short>` pattern stays — it's not required by Single mode but the SHA in the revision name makes `az containerapp revision list` greppable. The expand-contract migration policy stays — it's now the only thing keeping a rolled-back code revision compatible with the schema (it always was, but with traffic-weight rollback it had a fast escape hatch).

## What we lost

Documented in `docs/runbooks/deploy.md` § *What we gave up*. Summary: 3-5 min slower rollback; no pre-traffic smoke gate (the readiness probe is now the only health gate before user traffic reaches a new revision).

## Revisit conditions

If any of these happen, re-open the decision:

- The product moves to a real prod posture with SLOs that don't tolerate a 3-5-min rollback window.
- A regression that the readiness probe wouldn't catch ships to users (the readiness probe checks `/healthz`, not "the application is correct").
- A future feature wants to canary a new revision at, say, 10 % weight for an hour before shifting fully. That requires Multiple mode and a `trafficWeight` rule per revision.

Until any of those, Single mode is the simpler thing that fits.

## Local sweep

`az bicep build --file infra/main.bicep` clean. `uv run pre-commit run --all-files` clean. No Python tests touched (the deploy workflow + Bicep have no unit tests; verification is via `az bicep build` + the live deploy). The webapp suite is unaffected.
