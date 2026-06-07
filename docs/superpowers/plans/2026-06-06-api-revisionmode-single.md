# API revisionMode: Multiple → Single — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the `horizons-dev-api` Container App from `activeRevisionsMode: Multiple` to `Single`, removing the blue/green dance from `deploy.yml`. Bring the API in line with the worker (and the SPA's "overwrite in place" posture). Simplicity over the instant-rollback affordance; deliberate trade-off documented in the journal.

**Architecture:** With Single mode, ACA itself manages the revision lifecycle: each `az containerapp update` creates one new revision, waits for its readiness probe to pass, and auto-deactivates the previous. No PREV capture, no traffic pinning, no traffic shift, no stale-revision cleanup. The readiness probe (`/healthz` every 5s, failureThreshold 3, already in Bicep) is the only gate — if it fails ACA refuses to shift traffic and the previous revision continues to serve. Rollback becomes `az containerapp update --image …:sha-PREV` (~3-5 min) instead of a weight flip (~5s).

**Tech Stack:** Bicep (Microsoft.App/containerApps@2024-10-02-preview), GitHub Actions, az CLI, markdown docs.

**Scope check:** No application code changes. No Python tests affected — the repo has no tests exercising the deploy workflow or Bicep modules (`infra/` is verified via `az bicep build` only). The Playwright e2e and the integration suite run against a locally-booted stack, not Azure, so they're unaffected.

**Cleanup expectation:** No live blue/green state needs unwinding on Azure before this lands. The 2026-06-06 fix-revision-pileup step in `deploy.yml` ("Deactivate stale API revisions") already trims to NEW+PREV on every deploy. When this plan ships and the Bicep flip lands, ACA in Single mode will deactivate PREV on the next successful `az containerapp update`. No manual `az` cleanup needed.

---

## File Structure

**Modify:**
- `infra/modules/container-app-api.bicep` — flip `activeRevisionsMode` + rewrite the two related comment blocks. The `traffic[]`-omitted comment becomes obsolete (Single mode's platform default `latestRevision: true, weight: 100` is exactly what we want).
- `.github/workflows/deploy.yml` — delete 6 blue/green steps from `deploy-services`, replace with one `az containerapp update` + one post-update smoke. Rewrite the header comment block describing the pipeline shape.
- `docs/runbooks/deploy.md` — rewrite §`deploy-services (API blue/green)` and §`Manual rollback`. Update the "healthy run" table.
- `docs/runbooks/reseed.md` — update the "FATAL: remaining connection slots" failure-mode write-up (worker + API both Single now; this failure mode is historical).
- `docs/runbooks/local-dev.md` — line 180 mentions "the blue/green pipeline for staging"; drop "blue/green".
- `infra/README.md` — line 38 architectural-decisions bullet.
- `infra/modules/container-app-worker.bicep` — line 83 comment references "the API's blue/green dance"; drop the framing.
- `infra/modules/migration-job.bicep` — comments reference "the traffic shift"; reword to "the API revision update".
- `CLAUDE.md` — lines 9 and 109 mention "blue/green" in the project summary and `deploy.yml` runbook pointer.
- `docs/plan/improvement-plan.md` — line 22 (locked-in plan item 10) and line 157 (WU6.3 acceptance criteria).
- `docs/plan/evaluation-report.md` — **leave alone**. It's a frozen 2026-06-04 baseline snapshot; rewriting it would falsify the historical record. The new journal entry is where the change-of-mind lives.

**Create:**
- `journal/260606-api-revisionmode-single.md` — change-of-mind write-up. Why this supersedes the `journal/260606-fix-revision-pileup.md` design that kept the API in Multiple. The trade-offs (lost instant rollback, lost pre-traffic smoke gate) and why they're acceptable for the demo+small-prod posture.

**Untouched (intentional):**
- `journal/260606-fix-revision-pileup.md` — historical; reasoning was correct given that day's constraints. The new journal entry references it.
- Other journal entries — historical artefacts; references to "blue/green" are accurate-as-of-write.
- `docs/plan/discussions/04-azure-cicd.md` — frozen subagent discussion log from the 2026-06-04 engineering-team evaluation.

---

## Task 1: Flip the Bicep

**Files:**
- Modify: `infra/modules/container-app-api.bicep`

- [ ] **Step 1: Read the current header comment + the activeRevisionsMode line + the traffic[] comment block**

Already known from reconnaissance (L1–6, L94, L100–112). Confirming with a fresh Read before editing.

- [ ] **Step 2: Rewrite the header comment block (L1–6)**

Replace the existing 6-line header with this 5-line version (no mention of revisions — the file's job is "API container app"):

```bicep
// Container App — public REST API (FastAPI / uvicorn).
//
// Per docs/RFC-4 services.md, this is the single HTTP surface every client
// talks to. External ingress on :8000 → port 8000 inside the container.
```

(Drop the original L5–6 "Multiple revisions per locked-in plan item 10…" sentence.)

- [ ] **Step 3: Flip `activeRevisionsMode`**

```bicep
      activeRevisionsMode: 'Single'
```

- [ ] **Step 4: Replace the `traffic[]`-omitted comment block (L100–112) with a one-liner**

The 13-line rationale for omitting `traffic[]` was specific to Multiple mode (imperative traffic shifts in deploy.yml). Single mode wants the platform default (`latestRevision: true, weight: 100`), which is what omitting `traffic[]` already produces. Replace with:

```bicep
        // No `traffic[]` block — Single mode defaults to
        // `latestRevision: true, weight: 100`, which is correct.
```

- [ ] **Step 5: Verify Bicep still compiles**

Run: `az bicep build --file infra/main.bicep`
Expected: exit 0, no warnings beyond pre-existing ones (the file emits a `compiledFileName` next to main.bicep — `infra/main.json` may or may not exist depending on prior state; either is fine).

- [ ] **Step 6: Commit**

```bash
git add infra/modules/container-app-api.bicep
git commit -m "fix(infra): flip API container app to activeRevisionsMode Single

Drop the blue/green setup. The demo's rollback budget (3-5 min via
re-deploy) is shorter than the maintenance cost of the traffic-shift
+ stale-revision-cleanup machinery in deploy.yml. The worker is
already Single (since the WU6.6 fix-revision-pileup change); align
the API with it. The deploy.yml surgery lands in a follow-up commit.
"
```

---

## Task 2: Strip the blue/green dance from deploy.yml

**Files:**
- Modify: `.github/workflows/deploy.yml`

The `deploy-services` job currently has 8 steps (Azure login + 7 blue/green steps). After this task it has 4: Azure login, `az containerapp update --api`, smoke against the stable API FQDN, `az containerapp update --worker`.

- [ ] **Step 1: Rewrite the file-header comment block (L16–47)**

Replace L16–47 of the existing comment block (the `# What it does, in order:` section through `# follow-up flagged in docs/runbooks/deploy.md).` end of the comment) with:

```yaml
# What it does, in order:
#   prepare-infra  → Bicep deploy (idempotent infra reconciliation) +
#                    `az containerapp job start` for the migration Job
#                    (WU6.4) + the demo-accounts seed Job. Captures the
#                    storage account name from Bicep outputs for the SPA
#                    job.
#   deploy-services → update API + worker container apps:
#                       1. `az containerapp update --image :sha-X` on the
#                          API. Both apps are `activeRevisionsMode: Single`;
#                          ACA creates a new revision, waits for the
#                          readiness probe to pass, then shifts traffic
#                          and deactivates the previous revision.
#                       2. Smoke-test the API's stable FQDN — `/healthz`
#                          and `/openapi.json`. Post-shift; this is a
#                          tripwire, not a gate.
#                       3. `az containerapp update --image :sha-X` on the
#                          worker.
#   deploy-spa    → parallel job: build the Vue bundle, upload to the
#                   storage account's $web container, purge Front Door
#                   cache for /, /index.html, /config.json. Non-blocking
#                   on deploy-services: if the API deploy fails, the SPA
#                   deploy still runs (`needs:` lists `prepare-infra`,
#                   not `deploy-services`).
#
# Rollback is `az containerapp update --image :sha-PREV` against
# whichever app regressed. ~3-5 min wall-clock; see docs/runbooks/deploy.md.
```

(Drop the original "blue/green of the API container app" multi-paragraph description.)

- [ ] **Step 2: Rename the `deploy-services` job display name (L359)**

```yaml
    name: Deploy API + worker
```

(Was: `Deploy API (blue/green) + worker`.)

- [ ] **Step 3: Delete the `Capture previous active API revision` step (L373–391)**

Remove the entire `- name: Capture previous active API revision` step (the `id: prev` step) including the run script.

- [ ] **Step 4: Delete the `Pin traffic to previous revision` step (L393–408)**

Remove the entire `- name: Pin traffic to previous revision` step including its `if:` guard and run script.

- [ ] **Step 5: Replace the `Create new API revision (0% traffic)` step (L410–426) with a Single-mode update**

```yaml
      - name: Update API revision
        id: api_update
        # Single mode: ACA creates a new revision, waits for the readiness
        # probe to pass, then shifts traffic to it and deactivates the
        # previous revision. The --revision-suffix isn't required (ACA
        # auto-generates one) but is kept for traceability — `sha-<short>`
        # in the revision name makes "which build is this?" a one-liner.
        run: |
          set -euo pipefail
          az containerapp update \
            --name "$API_APP_NAME" \
            --resource-group "$AZURE_RESOURCE_GROUP" \
            --image "ghcr.io/johnmathews/horizons-api:sha-$SHORT_SHA" \
            --revision-suffix "sha-$SHORT_SHA"
          NEW_REVISION="${API_APP_NAME}--sha-$SHORT_SHA"
          echo "name=$NEW_REVISION" >> "$GITHUB_OUTPUT"
          echo "Updated to revision: $NEW_REVISION"
```

- [ ] **Step 6: Replace the `Smoke test new revision` step (L428–469) with a stable-FQDN tripwire**

In Single mode there is no pre-traffic FQDN window (ACA shifts traffic on its own once readiness is green). The smoke step becomes a tripwire against the app's stable FQDN — if the new revision is broken in a way that escaped the readiness probe, this fails the workflow and a human reacts. No automated rollback.

```yaml
      - name: Smoke-test API (stable FQDN, post-shift tripwire)
        id: smoke
        # ACA has already shifted traffic by the time `az containerapp
        # update` returned. This curl confirms the new revision is the
        # one that the stable FQDN now resolves to, and gives a workflow
        # log line a human can grep if a user-facing alert fires later.
        # NOT a gate: if this fails, manually re-deploy the prior SHA.
        run: |
          set -euo pipefail
          API_FQDN=$(az containerapp show \
            --name "$API_APP_NAME" \
            --resource-group "$AZURE_RESOURCE_GROUP" \
            --query "properties.configuration.ingress.fqdn" -o tsv)
          echo "Smoke-testing https://$API_FQDN"

          # Allow time for the post-shift connection drain on PREV.
          for i in $(seq 1 12); do
            if curl --fail-with-body --silent --max-time 5 \
                "https://$API_FQDN/healthz" > /dev/null; then
              echo "[$i/12] /healthz OK"
              break
            fi
            echo "[$i/12] /healthz not ready yet"
            sleep 5
          done

          curl --fail-with-body --max-time 10 \
            "https://$API_FQDN/healthz"
          echo
          curl --fail-with-body --silent --max-time 10 \
            "https://$API_FQDN/openapi.json" > /dev/null
          echo "/openapi.json reachable"
```

- [ ] **Step 7: Delete the `Shift traffic to new revision` step (L471–493)**

Remove the entire `- name: Shift traffic to new revision` step including its `id: shift` and run script. Single mode handles this.

- [ ] **Step 8: Delete the `Rollback traffic on failure` step (L495–513)**

Remove the entire `- name: Rollback traffic on failure` step. There is no traffic to roll back — Single mode owns the shift, and on a failed shift it keeps the previous revision serving. The smoke step's failure becomes the operator's signal to re-deploy the previous SHA.

- [ ] **Step 9: Update the `Update worker revision` step's comment (L515–522)**

The existing comment references the worker's `Single` mode by contrast with the API. Now both are Single; the comment is stale. Replace with:

```yaml
      - name: Update worker revision
        # Same shape as the API update — Single mode, ACA auto-shifts
        # and deactivates the previous revision once readiness is green.
        # No traffic management because the worker has no ingress
        # (ADR-0001: one always-on replica).
```

(The actual `az containerapp update` command for the worker, L523–529, is unchanged.)

- [ ] **Step 10: Delete the `Deactivate stale API revisions` step (L531–576)**

Remove the entire `- name: Deactivate stale API revisions` step including the `if:` guard and the run script. Single mode auto-deactivates.

- [ ] **Step 11: Update the `deploy-spa` job comment (L580)**

```yaml
    # SPA deploy is independent of the API revision update. By
    # depending on `prepare-infra` (not `deploy-services`), this job
    # runs in parallel with `deploy-services` and still ships even if
    # the API deploy fails.
```

(Was: "API blue/green sequence".)

- [ ] **Step 12: Lint the workflow**

Run: `uv run pre-commit run --all-files`
Expected: all hooks pass. The yaml hooks (`check-yaml`, `prettier` if wired for yaml) will validate the file. If prettier reflows blocks, accept the reflow.

- [ ] **Step 13: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "fix(ci): drop blue/green dance from deploy.yml; rely on Single mode

deploy-services now does:
  az containerapp update (API)  →  ACA auto-shifts on readiness
  smoke /healthz + /openapi.json on the stable FQDN (tripwire)
  az containerapp update (worker)

Removed: PREV capture, traffic pin, smoke against per-revision FQDN,
explicit traffic shift, on-failure traffic rollback, stale-revision
cleanup loop. ACA owns all of those in Single mode.

Rollback is now: az containerapp update --image :sha-PREV (~3-5 min).
"
```

---

## Task 3: Update `docs/runbooks/deploy.md`

**Files:**
- Modify: `docs/runbooks/deploy.md`

This is the runbook the on-call operator opens during a deploy. It must reflect the new shape exactly.

- [ ] **Step 1: Update the audience line (L3–4)**

```markdown
_Audience: operator running or watching `.github/workflows/deploy.yml`. Companion to [migrations.md](./migrations.md),
which covers the expand-contract rule that keeps rollbacks safe._
```

(Was: "blue/green rollbacks". The expand-contract rule still applies — it's how a redeploy-the-previous-SHA rollback stays compatible with a forward migration.)

- [ ] **Step 2: Update the pipeline ASCII tree (L15–19)**

```
gate ─┬──> prepare-infra (Bicep + migration ACA Job)
      ├──> deploy-services    (API revision update + worker update)   parallel
      └──> deploy-spa         (build, upload to $web, purge cache)    parallel
```

- [ ] **Step 3: Replace §`deploy-services (API blue/green)` (L34–63) with §`deploy-services (API)`**

Delete L34–63 and insert:

```markdown
### deploy-services (API)

Both container apps run `activeRevisionsMode: Single`. The flow is the same for both, and minimal: a single `az containerapp update --image ghcr.io/johnmathews/horizons-api:sha-<short> --revision-suffix sha-<short>` creates a new revision named `horizons-dev-api--sha-<short>`, ACA waits for its `/healthz` readiness probe to come green (configured in `infra/modules/container-app-api.bicep`: `periodSeconds: 5`, `failureThreshold: 3`), shifts 100 % of traffic to the new revision, and deactivates the previous one. The `--revision-suffix` isn't required — ACA auto-generates a suffix when omitted — but is kept for traceability: a `sha-<short>` in the revision name makes "which build is this?" a one-liner.

After the update returns, a smoke step curls the **stable** API FQDN (`<app>.<env-defaultDomain>`, not a per-revision FQDN) and asserts `/healthz` 200 OK and `/openapi.json` reachable. This is a tripwire, not a gate: traffic has already shifted by the time `az containerapp update` returned. If the smoke fails, the workflow fails, and the operator manually re-deploys the previous SHA (see [Manual rollback](#manual-rollback)). The pre-shift smoke against a per-revision FQDN that used to live here is gone — Single mode owns the shift, so there is no 0 %-weight window to test in.
```

- [ ] **Step 4: Replace §`deploy-services (worker)` (L64–73) with a one-paragraph version**

```markdown
### deploy-services (worker)

Same shape as the API: `az containerapp update --image ghcr.io/johnmathews/horizons-worker:sha-<short> --revision-suffix sha-<short>`. The worker has one always-on replica per [ADR-0001](../adrs/0001-worker-shape.md) and no ingress, so there is no smoke step — ACA's readiness probe is the only gate.
```

- [ ] **Step 5: Rewrite the "healthy run" table (L108–120)**

Replace the existing table rows for `deploy-services` with these three (the prepare-infra and deploy-spa rows are unchanged):

```markdown
| `deploy-services` | Update API revision                  | The `az containerapp update` JSON response with `provisioningState: "Succeeded"`; then `Updated to revision: horizons-dev-api--sha-<short>`. |
| `deploy-services` | Smoke-test API (stable FQDN)         | `Smoke-testing https://horizons-dev-api.<env>.westeurope.azurecontainerapps.io` → `[N/12] /healthz OK` → `/openapi.json reachable`           |
| `deploy-services` | Update worker revision               | The `az containerapp update` JSON response with `provisioningState: "Succeeded"`                                                              |
```

- [ ] **Step 6: Rewrite §`Manual rollback` (L122–177)**

Replace the entire section (everything from `## Manual rollback` through the SPA bundle bullet) with:

```markdown
## Manual rollback

There is no automatic rollback. If a deploy completes but a regression surfaces (alert fires, the smoke step failed, user report), roll back by re-deploying the previous image SHA.

Step 1 — find the previous SHA. The currently-active revision encodes the deployed SHA in its name (`horizons-dev-api--sha-<short>`). The previous deployment's commit SHA is whatever sat at `HEAD~1` on `main` when the current revision was built; `git log --oneline -5 main` is the fastest way to get it.

Step 2 — re-deploy. Manually trigger the deploy workflow against the previous build's commit, or, faster, run the `az containerapp update` directly with the previous SHA:

```bash
PREV_SHORT_SHA=<12-char short SHA of the previous build>

# API
az containerapp update \
  --name horizons-dev-api \
  --resource-group horizons-nonprod \
  --image "ghcr.io/johnmathews/horizons-api:sha-$PREV_SHORT_SHA" \
  --revision-suffix "sha-$PREV_SHORT_SHA"

# Worker, if it also regressed
az containerapp update \
  --name horizons-dev-worker \
  --resource-group horizons-nonprod \
  --image "ghcr.io/johnmathews/horizons-worker:sha-$PREV_SHORT_SHA" \
  --revision-suffix "sha-$PREV_SHORT_SHA"
```

ACA creates the rollback revision, waits for readiness, shifts traffic. ~3-5 min wall-clock per app. The image must already be in GHCR — the rollback target is whatever the previous successful `build-and-push.yml` pushed, so any SHA that ever made it to `main` is available.

### What rollback does NOT undo

- **Database migrations.** Alembic upgrades are not auto-reverted on a code rollback. The expand-contract policy in [migrations.md](./migrations.md) is what keeps the rolled-back code compatible with the migrated schema. If a migration broke things, see that runbook for the downgrade procedure.
- **SPA bundle.** The current pipeline overwrites `$web` in place with no versioning. Rolling back the SPA requires re-running the build at the previous SHA. Future work: keep the previous bundle under a versioned prefix so a `cp` between prefixes is the rollback.

### What we gave up

The earlier version of this pipeline ran `activeRevisionsMode: Multiple` on the API, kept PREV warm at 0 % weight, and rolled back with a 5-second `az containerapp ingress traffic set` weight flip. We dropped that on 2026-06-06 (see `journal/260606-api-revisionmode-single.md`): the wall-clock difference (3-5 min vs. ~5s) doesn't pay for the maintenance cost of the traffic-shift + stale-revision-cleanup machinery in `deploy.yml` at demo-scale. If we ever need sub-minute rollback for prod, revisit the decision then.
```

- [ ] **Step 7: Lint**

Run: `uv run pre-commit run --all-files`
Expected: all hooks pass (markdown hooks if any).

- [ ] **Step 8: Commit**

```bash
git add docs/runbooks/deploy.md
git commit -m "docs(runbooks): rewrite deploy.md for activeRevisionsMode Single

deploy-services becomes 'az containerapp update + smoke + worker
update'. Manual rollback is 'redeploy previous SHA' rather than a
traffic weight flip. Cross-references the journal entry explaining
the trade-off.
"
```

---

## Task 4: Update the other docs that mention blue/green or revisionMode

**Files:**
- Modify: `docs/runbooks/reseed.md`
- Modify: `docs/runbooks/local-dev.md`
- Modify: `infra/README.md`
- Modify: `infra/modules/container-app-worker.bicep`
- Modify: `infra/modules/migration-job.bicep`
- Modify: `CLAUDE.md`
- Modify: `docs/plan/improvement-plan.md`

These are all small surgical edits.

- [ ] **Step 1: `docs/runbooks/reseed.md` — update the connection-slot failure mode**

Read L175–197 first. Then replace the "Root cause" line through the `Same loop for horizons-dev-api…` line with:

```markdown
Postgres is out of non-superuser connection slots. **As of 2026-06-06 this failure mode is historical** — both the worker and the API now run `activeRevisionsMode: Single`, so ACA auto-deactivates the previous revision on every deploy and revisions cannot accumulate. If it does recur (a config drift, a manual `az containerapp update --activeRevisionsMode Multiple`), the cleanup loop is:

```bash
KEEP_WORKER=horizons-dev-worker--sha-<latest>
az containerapp revision list --name horizons-dev-worker -g horizons-nonprod \
  --query "[?properties.active && name != '$KEEP_WORKER'].name" -o tsv | \
  while read r; do
    az containerapp revision deactivate \
      --name horizons-dev-worker -g horizons-nonprod --revision "$r"
  done
# Same loop for horizons-dev-api with that app's latest revision.
```
```

(The cleanup loop stays as the recovery procedure; the "what happened" narrative shifts to "historical".)

- [ ] **Step 2: `docs/runbooks/local-dev.md` — drop "blue/green" from L180**

Read L180 first to get the surrounding context. The line currently reads `docs/runbooks/deploy.md` — the blue/green pipeline for staging /`. Edit to:

```markdown
- `docs/runbooks/deploy.md` — the staging / production deploy pipeline (Bicep + container app updates + SPA upload).
```

(If the original line continues to a second line, fold that into the same bullet — the file uses one-line list items per the doc-style memory.)

- [ ] **Step 3: `infra/README.md` — update the architectural-decisions bullet on L37–39**

```markdown
- **Revision mode (locked-in plan §10, revised 2026-06-06):** Both
  container apps run `activeRevisionsMode: Single`. ACA owns the
  revision shift and previous-revision deactivation on every update.
  Rollback is `az containerapp update --image :sha-PREV`; see
  `docs/runbooks/deploy.md`. The original plan called for `Multiple`
  to support traffic-weight blue/green; we walked that back — the
  maintenance cost in `deploy.yml` wasn't worth the 5-second-vs-
  5-minute rollback delta at demo scale.
```

- [ ] **Step 4: `infra/modules/container-app-worker.bicep` — drop the "API's blue/green dance" framing at L83**

Read L80–90 first. The comment currently contrasts the worker against "the API's blue/green dance". Edit to drop that contrast — both apps are Single now:

```bicep
      // activeRevisionsMode: Single — one always-on replica per ADR-0001,
      // no ingress to load-balance over. ACA auto-deactivates the
      // previous revision once the new replica is healthy.
```

(Trim the surrounding lines to remove any other "API's blue/green" mention. Aim for a 2-3 line comment.)

- [ ] **Step 5: `infra/modules/migration-job.bicep` — reword the "traffic shift" mentions at L121 + L129**

Read L115–135 first. Reword "the traffic shift" → "the API revision update" in both places. The migration-runs-before-deploy-services ordering is unchanged; only the downstream-step's name changed.

- [ ] **Step 6: `CLAUDE.md` — drop "blue/green" from L9 + L109**

L9: Replace `the Bicep IaC + \`deploy.yml\` blue/green pipeline,` with `the Bicep IaC + \`deploy.yml\` deploy pipeline,`.

L109: Replace `See \`docs/runbooks/deploy.md\` for the blue/green revision flip + SPA upload + Front Door purge sequence` with `See \`docs/runbooks/deploy.md\` for the API + worker update + SPA upload + Front Door purge sequence`.

- [ ] **Step 7: `docs/plan/improvement-plan.md` — update locked-in plan item §10 (L22) and WU6.3 acceptance criteria (L157)**

Read L20–25 first. The locked-in item 10 currently ends with `Revision-based rollback via \`activeRevisionsMode: Multiple\` + \`az containerapp ingress traffic set\`.`. Edit to:

```markdown
10. **IaC: Bicep**, in `infra/` co-located with services. CI/CD: GitHub Actions, OIDC federation to Azure (no client secrets). Images to `ghcr.io/johnmathews/horizons`. Migrations run as a separate ACA Job before the API revision update. Rollback is `az containerapp update --image :sha-PREV` against the regressed app (revised 2026-06-06 from `activeRevisionsMode: Multiple` + traffic-weight flip — see `journal/260606-api-revisionmode-single.md`).
```

Read L155–160 first. WU6.3 acceptance criteria currently describes the blue/green sequence (a) → (f). Edit to:

```markdown
**WU6.3 · `deploy.yml` with revision-based rollback.** Depends on: WU6.0, WU6.1, WU6.2. Acceptance: on push to main after build succeeds, runs `az deployment group create` against the target env to (a) ensure infra is current, (b) start a one-shot migrations ACA Job, (c) `az containerapp update` the API container app (Single mode: ACA creates a new revision, waits for readiness, shifts traffic, deactivates the previous), (d) post-shift smoke against the stable API FQDN as a tripwire, (e) `az containerapp update` the worker. Rollback is operator-driven: re-deploy the previous SHA (see `docs/runbooks/deploy.md`). SPA build job uploads `webapp/dist/` to `$web` blob container and purges Front Door cache for `index.html` + `config.json`. (Revised 2026-06-06 from the original blue/green sequence — see `journal/260606-api-revisionmode-single.md`.)
```

- [ ] **Step 8: Verify the local sweep**

Run all three in sequence (they're cheap and document-only):

```bash
az bicep build --file infra/main.bicep
uv run pre-commit run --all-files
```

Expected: both clean. The webapp lint/build/test do not depend on these files — skip them.

- [ ] **Step 9: Commit**

```bash
git add docs/runbooks/reseed.md docs/runbooks/local-dev.md \
        infra/README.md infra/modules/container-app-worker.bicep \
        infra/modules/migration-job.bicep \
        CLAUDE.md docs/plan/improvement-plan.md
git commit -m "docs: align supporting docs + bicep comments with API Single mode

reseed.md: connection-slot failure mode marked historical.
local-dev.md, CLAUDE.md: drop 'blue/green' phrasing.
infra/README.md: revised locked-in §10 to reflect the 2026-06-06 flip.
container-app-worker.bicep, migration-job.bicep: drop comments that
  contrast against the API's old blue/green dance.
improvement-plan.md: locked-in §10 + WU6.3 acceptance criteria
  reflect the new shape with a pointer to the journal entry.
"
```

---

## Task 5: Write the change-of-mind journal entry

**Files:**
- Create: `journal/260606-api-revisionmode-single.md`

The new entry sits alongside `journal/260606-fix-revision-pileup.md` and supersedes the API-half of that earlier decision. The worker-half of that earlier decision (worker → Single) stays — it was right then and it's still right.

- [ ] **Step 1: Write the entry**

```markdown
# 2026-06-06 — flip the API to `activeRevisionsMode: Single`

Earlier today's [`260606-fix-revision-pileup.md`](./260606-fix-revision-pileup.md) walked the worker to Single and kept the API in Multiple, adding a `Deactivate stale API revisions` step to the end of `deploy-services` so the pile of zero-weight revisions stops growing. That decision was correct given the immediate goal: stop the reseed Job from running out of Postgres connection slots an hour before the demo.

Re-reading the change-set after the deploy went green, the API-Multiple side of it doesn't pay for itself. This entry captures what we changed and why.

## What changed

- `infra/modules/container-app-api.bicep` — `activeRevisionsMode: 'Multiple'` → `'Single'`.
- `.github/workflows/deploy.yml` — `deploy-services` collapses from 8 steps to 4. Removed: PREV capture, traffic pin, per-revision-FQDN smoke, explicit traffic shift, on-failure traffic rollback, stale-revision cleanup loop. The remaining 4: Azure login, `az containerapp update --api`, stable-FQDN tripwire smoke, `az containerapp update --worker`.
- `docs/runbooks/deploy.md` — `deploy-services (API blue/green)` section rewritten as `deploy-services (API)`. Manual rollback section rewritten as "redeploy the previous SHA".

## Why now, not at the next deploy

The Multiple-mode API was load-bearing for **instant rollback via traffic-weight flip** (~5s) and for a **pre-traffic smoke gate** against the new revision's unique FQDN. Both of those are real properties, but neither is paying its rent here.

**Instant rollback.** The replacement — `az containerapp update --image :sha-PREV` — takes 3-5 min. The delta is real (~3-5 min vs. ~5s). At demo scale the operator workflow on a regression is "alert fires → 30-60s of "is this real?" investigation → trigger the rollback", and the regression is a couple-minute incident regardless of whether the rollback machinery takes 5s or 5 min. The 5s number is the upper bound of an idealised case where the operator knew which revision was bad the moment the alert fired. We are not in that case.

**Pre-traffic smoke gate.** The smoke test the previous shape ran was a single curl of `/healthz` + `/openapi.json`. Both endpoints are also targets of the readiness probe ACA already runs every 5s with `failureThreshold: 3` (in `infra/modules/container-app-api.bicep` L194–203). If a revision fails the smoke step's `/healthz`, it would also fail the readiness probe, ACA would not shift traffic, and the previous revision would continue to serve. The smoke gate was, in practice, redundant with the readiness probe.

The Multiple-mode setup is therefore paying for a ~3-5-minute improvement in rollback wall-clock and a redundant smoke gate, in exchange for:
- 6 imperative `az containerapp` steps in `deploy.yml` (~140 lines)
- A "deactivate stale revisions" loop that *also* exists because of Multiple mode
- The reseed-Job-connection-slot failure mode that bit us this morning
- Several pages of `docs/runbooks/deploy.md` describing the dance

Not a great trade at our scale.

## What we kept

The worker stays in Single (no change to `container-app-worker.bicep`). The `--revision-suffix sha-<short>` pattern stays — it's not required by Single mode but the SHA in the revision name makes `az containerapp revision list` greppable. The expand-contract migration policy stays — it's now the only thing keeping a rolled-back code revision compatible with the schema (it always was, but with traffic-weight rollback it had a fast escape hatch).

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
```

- [ ] **Step 2: Lint**

Run: `uv run pre-commit run --all-files`
Expected: clean. Markdown hooks (if any) catch trailing whitespace + EOF newline.

- [ ] **Step 3: Commit**

```bash
git add journal/260606-api-revisionmode-single.md
git commit -m "docs(journal): record the API → Single revisionMode flip

Companion to 260606-fix-revision-pileup.md: that entry's
worker → Single half stays. This entry walks back the API → Multiple
half that the same morning's session chose, with the why-we-changed.
"
```

---

## Task 6: Final verification + push

- [ ] **Step 1: Local sweep**

Run sequentially:

```bash
uv run pytest -m "not integration"
uv run ruff check .
uv run pyright
uv run pre-commit run --all-files
az bicep build --file infra/main.bicep
```

Expected: all pass. Python tests are unaffected (we touched no Python); the webapp suite is unaffected (we touched no `packages/horizons-webapp/`). The sweep is for the global "before push" guard per [feedback_run_precommit_before_push].

- [ ] **Step 2: Eyeball the diff against `main`**

Run: `git log --oneline main..HEAD` and confirm the commits are the five from Tasks 1, 2, 3, 4, 5.

Run: `git diff main..HEAD --stat` and sanity-check the line counts:
- `infra/modules/container-app-api.bicep` — small (~15 lines changed)
- `.github/workflows/deploy.yml` — large net deletion (~140 lines removed, ~50 added)
- `docs/runbooks/deploy.md` — large net deletion (~80 lines removed, ~40 added)
- Other docs/bicep — small (~10-30 lines each)
- `journal/260606-api-revisionmode-single.md` — new file, ~80 lines

If the workflow file's deletion count looks off, re-read the file and confirm the 6 blue/green steps are gone.

- [ ] **Step 3: Push to main**

The repo's CI cadence (per CLAUDE.md) is fast-forward to main and direct push. From the worktree if used, do the merge through the main checkout per the documented sequence; otherwise push from main directly:

```bash
git push origin main
```

The push triggers `ci.yml` and `webapp.yml` on the head SHA. They should both go green (nothing in either workflow touches Bicep or the deploy workflow).

- [ ] **Step 4: Trigger a real staging deploy**

The next push-to-main would normally fire `build-and-push.yml` → `deploy.yml` automatically. If this plan's final commit *is* that push, the chain runs automatically. Otherwise, manually:

```bash
gh workflow run deploy.yml --field environment=staging
gh run watch
```

Expected: `deploy-services` completes in ~3-5 min (was ~5-8 min with the blue/green dance). The revision list afterwards shows exactly one active API revision (the new `--sha-<short>`) and one active worker revision. Verify:

```bash
az containerapp revision list \
  --name horizons-dev-api \
  --resource-group horizons-nonprod \
  --query "[?properties.active].{name:name, weight:properties.trafficWeight}" \
  -o table
```

Expected: one row, weight 100, name `horizons-dev-api--sha-<short>`.

If anything goes sideways, the rollback documented in `docs/runbooks/deploy.md` is now `az containerapp update --image :sha-PREV` (which is *exactly* what this plan ships).

---

## Self-review checklist

- **Spec coverage.** Six tasks cover the API Bicep flip, the workflow rewrite, the operator runbook, the supporting docs, the journal entry, and the verify-and-deploy. Tradeoffs are documented in both `docs/runbooks/deploy.md` and the journal.
- **Placeholder scan.** No TBDs. All Bicep / YAML / markdown deltas are written out in full; no "similar to Task N" or "fill in details".
- **Type consistency.** No new types. Existing types/names (`API_APP_NAME`, `WORKER_APP_NAME`, `--revision-suffix`, `sha-<short>`) used consistently across tasks.
- **Spec gaps?** `evaluation-report.md` is intentionally left as a frozen baseline. `journal/260606-fix-revision-pileup.md` is intentionally left as a historical artefact pointed-to by the new journal entry. Both are called out in the File Structure section so the engineer doesn't second-guess.
