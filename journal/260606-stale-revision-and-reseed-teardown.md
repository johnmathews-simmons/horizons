# 2026-06-06 — stale API revision + reseed teardown blocker

*Last revised: 2026-06-06.*
*Path: journal/260606-stale-revision-and-reseed-teardown.md.*

Post-demo-prep session. Two bugs surfaced via the SPA failing to load `/v1/me/overview` and `/v1/documents`. Both are now fixed; one of the fixes is operator-only (live state) and the other is a code commit (`6e02027`). One structural follow-up remains, captured in the punch list.

## Symptoms

- `GET /v1/me/overview` and `GET /v1/documents` both returning 404 against `horizons-dev-api.prouddune-35523793.westeurope.azurecontainerapps.io`.
- `GET /openapi.json` confirmed: the deployed API surface was missing both routes (plus `/v1/documents/{id}/...`).
- After unblocking the routes, `/documents/<id>` sat on "Loading clauses…" with an empty version label.

## Cause 1 — Bicep flip never applied (live state stuck at Multiple)

`infra/modules/container-app-api.bicep:92` was changed from `activeRevisionsMode: 'Multiple'` to `'Single'` in commit `6a0a0d6` (today, 19:45 UTC). The intent per [`260606-api-revisionmode-single.md`](./260606-api-revisionmode-single.md) was that the next push would reconcile live state. It didn't.

`az containerapp show -g horizons-nonprod -n horizons-dev-api --query "properties.configuration"` reported `revisionsMode: "Multiple"` with traffic 100 % pinned to `horizons-dev-api--sha-aa0558a5baac` (created 19:39 UTC — pre-flip, pre-`/v1/me/overview`, pre-`/v1/documents`). Four newer revisions (`sha-120a1303e9d5` 20:00, `sha-35445fc1ba95` 20:54, `sha-695066834837` 21:06, `sha-1bba889342f8` 21:08) sat at 0 % weight.

`az deployment group list -g horizons-nonprod` showed the most recent ARM deployment of `container-app-api` was at **18:58:20 UTC** — almost an hour before the Bicep flip commit. Every push since was processed by `deploy.yml` but none re-ran the Bicep step.

### Why every post-flip deploy skipped Bicep

`.github/workflows/deploy.yml:152–178` ("Detect infra changes"):

```bash
if git diff --quiet HEAD~1 HEAD -- infra/ .github/workflows/deploy.yml; then
  echo "No infra changes vs HEAD~1 — skipping Bicep deploy, reusing previous outputs."
```

Pushes are batched. The Bicep-flip series was seven commits (`6a0a0d6` → `7db7a69` → `a5e32d7` → `124fc43` → `120a130` and two related), pushed together at 19:58 UTC. The head was `120a130` (a `docs(journal)` commit); HEAD~1 was `124fc43` (also docs, comment-only Bicep edits). The diff returned clean and Bicep was skipped. The actual `activeRevisionsMode: 'Single'` change sat 5 commits below HEAD and was never seen by the script.

Confirmed directly from the deploy run logs:

```
gh run view 27072383670 --log | grep "No infra changes vs HEAD~1"
→ "No infra changes vs HEAD~1 — skipping Bicep deploy, reusing previous outputs."
```

Subsequent pushes (`4281b53`, `35445fc`, `1bba889`, `6950668`) didn't touch `infra/` at all, so they kept skipping Bicep. Live state stayed Multiple with traffic pinned to `sha-aa0558a5baac` forever.

### Fix (live)

Operator-level, no code:

```bash
az containerapp revision set-mode --name horizons-dev-api -g horizons-nonprod --mode Single
```

After the flip ACA selected `horizons-dev-api--sha-1bba889342f8` (the most recently created revision) and shifted traffic to 100 %. That revision contains both routes, so the SPA's homepage overview and document list both started loading. Verified via `/openapi.json`: `/v1/me/overview`, `/v1/documents`, `/v1/documents/{document_id}`, and `/v1/documents/{document_id}/versions/{version_label}/clauses` all present.

### Fix (workflow) — punch list, not now

The HEAD~1 diff is the root cause and is now in the post-demo punch list (see "Pipeline confidence" addition below). Live state matches Bicep intent (both Single), so re-running Bicep would be a no-op — no urgency.

## Cause 2 — `create_demo_accounts.py --reset` blocked by `admin_access_log` RESTRICT

`./scripts/reseed_aca.sh --yes` was the natural next step to seed `document_versions` + `clauses` (the curated set seed only writes `documents` + `document_poll_schedule` without `--stage-synthetic-v2`). The Job's corpus wipe + reseed succeeded:

```
inserted: 10 document(s) / 10 schedule row(s)
staged:   5 synthetic v2 document(s) / 1773 clause row(s) / 32 change_event row(s)
```

…but `create_demo_accounts.py --reset` then failed:

```
psycopg.errors.RestrictViolation: update or delete on table "users" violates RESTRICT setting
  of foreign key constraint "admin_access_log_admin_id_fkey" on table "admin_access_log"
DETAIL: Key (id)=(019e9e62-503c-7696-98a8-851b5f188453) is referenced from table "admin_access_log".
```

`admin_access_log` was added in migration `0006_admin_access_log.py` with two `ON DELETE RESTRICT` FKs to `users` (`admin_id`, `target_user_id`) plus an append-only trigger that rejects `DELETE` on the audit table itself (`reject_admin_access_log_mutation()`). The admin demo account had been used by the impersonation flow at some point earlier in the day, leaving audit rows behind. `_teardown` couldn't delete the audit rows (trigger) and couldn't delete the users (FK).

`seed_e2e.py:_teardown` already solved this for the e2e fixtures: set `SET LOCAL session_replication_role = 'replica'` to bypass the trigger for the transaction, then `DELETE FROM admin_access_log WHERE admin_id IN … OR target_user_id IN …`, then delete the users. The fix mirrors that pattern exactly (`packages/horizons-api/scripts/create_demo_accounts.py:_teardown`, commit `6e02027`).

Effect on this session: the failed transaction rolled back cleanly, so the demo accounts were not changed and the in-browser admin session kept working. Once the build/deploy chain ships the new worker image, `reseed_aca.sh --yes` will run through to demo-account rotation in one shot.

## What's deployable vs. ingested

After the reseed, the corpus has 10 documents seeded. Five of them are the synthetic v1+v2 pairs the seed script stages from `data/samples/synthetic_v2/`:

- GB `28914588` (UK)
- DE `20951816`, FR `31702142`, IE `8064194`, IT `26863` (EU)

These five have `document_versions` and `clauses` and will render in `/documents/<id>`. The remaining 5 documents (e.g. the BEREC EU/BANKING fixture the user opened first) are metadata-only stubs — they need the ingestion worker to actually fetch from Lawstronaut, and the recent worker log tail shows only `/healthz` traffic. Whether the worker is mis-configured, paused, or just hasn't claimed yet is a separate investigation, deferred.

## Punch-list additions

Appending two items to [`260606-deploy-pipeline-end-to-end.md`](./260606-deploy-pipeline-end-to-end.md) → "Post-demo punch list":

- **Pipeline confidence:** replace `git diff HEAD~1 HEAD` in `deploy.yml`'s "Detect infra changes" step with a baseline that survives batched pushes. Options: diff `${{ github.event.workflow_run.head_commit.id }}..${{ github.event.workflow_run.head_commit.id }}~N` where N is the push depth (workflow_run.head_commit doesn't expose that directly); diff against the SHA of the most recent successful ARM deployment (pull from `az deployment group list`); or simply remove the optimisation and always run Bicep (it's idempotent; cost is ~mins/deploy).
- **Pipeline confidence (Job images):** `deploy-services` should also bump the Container App Jobs' images on every deploy, not just the API + worker container apps. Today the migration / seed-demo-accounts / reseed-corpus Jobs' images are declared in Bicep with `param image string = 'ghcr.io/johnmathews/horizons-worker:latest'` and only refresh when `prepare-infra` runs the full template. Since image-only commits don't touch `infra/`, those Jobs end up pinned to whatever image was current at the last actual Bicep deploy — hours or days stale. Burned us today: the fix in commit `6e02027` shipped to GHCR within minutes, but the reseed Job kept running `sha-ea8f494ebe26` (the previous day's image) and re-hit the original error. Unblocked manually with `az containerapp job update --name horizons-dev-reseed-corpus -g horizons-nonprod --image ghcr.io/johnmathews/horizons-worker:sha-6e020273ae92`. Fix: `deploy-services` needs an `az containerapp job update --image` call for each of the three Jobs alongside the existing API + worker updates. Independent of the HEAD~1 baseline fix above — the Job-image staleness re-fires on image-only pushes regardless.
- **Migration robustness / safer reseed:** the `seed_e2e.py:_teardown` and `create_demo_accounts.py:_teardown` divergence is a smell. Either factor the user-row cleanup (incl. `admin_access_log` clear) into a shared helper, or document the rule "any teardown that deletes from `users` must first DELETE from `admin_access_log` under `session_replication_role = 'replica'`". The trigger + FK shape is non-obvious and the next teardown script will trip the same wire.

## Commits

- `6e02027` `fix(scripts): clear admin_access_log rows before users DELETE in reseed`

## Pointers

- Earlier today's revisionMode decision: [`260606-api-revisionmode-single.md`](./260606-api-revisionmode-single.md)
- Punch list owner: [`260606-deploy-pipeline-end-to-end.md`](./260606-deploy-pipeline-end-to-end.md) (additions below "Pipeline confidence" and "Migration robustness")
- The teardown pattern the fix mirrors: `packages/horizons-api/scripts/seed_e2e.py:_teardown` (L125–204)
