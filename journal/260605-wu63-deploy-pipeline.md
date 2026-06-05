# WU6.3 — deploy.yml with revision-based rollback + SPA deploy

*Session 2026-06-05. Branch `wu6.3-deploy-pipeline` (Session J).*

The staging deploy pipeline. Triggers on `workflow_run` after
`build-and-push.yml` (WU6.2) succeeds on `main`, runs the Bicep
deployment to reconcile infra, kicks the migration ACA Job (WU6.4),
performs a blue/green API revision flip, updates the worker
revision, and — in parallel with the API/worker job — builds the
Vue SPA, uploads it to the storage `$web` container, and purges
Front Door cache. All Track 6 work units are now landed.

External verification (`gh workflow run deploy.yml --field
environment=staging`) is the user's post-merge step; the local
gate is YAML parse + `az bicep build` + the Python sweep + a full
pre-commit pass.

## What shipped

```
.github/workflows/deploy.yml             NEW — staging deploy pipeline.
                                         Three jobs: gate, prepare-infra
                                         (Bicep + migration), deploy-services
                                         (API blue/green + worker),
                                         deploy-spa (build/upload/purge).
docs/runbooks/deploy.md                  NEW — operator runbook. Pipeline
                                         walkthrough, manual rollback
                                         procedure, production cutover
                                         follow-up, prerequisites table.
infra/modules/container-app-api.bicep    Module tweak — drop the
                                         `traffic[]` block from the API's
                                         ingress so deploy.yml is the
                                         source of truth for traffic
                                         management. See "Bicep tweak"
                                         below.
```

## Architectural decisions reflected

1. **`workflow_run` trigger gated by upstream conclusion.** Locked-in
   plan item 10 mandates the post-build deploy. `workflow_run` is the
   GH-native way to chain workflows; it fires on every upstream
   completion regardless of outcome, so the `gate` job's `if:` filter
   is what actually drops failed builds. `workflow_dispatch` exists
   alongside as the manual re-run path with an explicit `environment`
   input (only `staging` is wired; the choice list is the place where
   `production` lands when the prod follow-up runs).
2. **Imperative traffic management.** Locked-in plan item 10 calls for
   "revision-based rollback via `az containerapp ingress traffic
   set`". For that to work, the Bicep cannot declare
   `traffic: [{latestRevision: true, weight: 100}]` because every
   subsequent Bicep deploy would re-pin traffic to whichever revision
   the platform considers "latest", overriding any imperative shift
   the workflow performed. Dropping the property from the ingress
   block means ARM incremental mode preserves the live traffic state
   on subsequent deploys; on the first deploy ever, ACA's platform
   default (`latestRevision: true, weight: 100`) applies, which is
   the correct bootstrap behaviour. The block is the only non-trivial
   Bicep change in this WU and the only one flagged for review.
3. **Three-job topology with `prepare-infra` as the shared root.**
   The user's prescribed step ordering (Bicep → migration → API
   revision → smoke → shift → worker → SPA) describes a logical
   sequence; mechanically, `deploy-services` and `deploy-spa` should
   not block each other (locked-in: "if the API deploy fails, the
   SPA deploy still runs"). Splitting the shared prefix
   (Bicep + migration) into a separate `prepare-infra` job lets both
   downstream jobs depend on it without depending on each other —
   true parallelism, but no Bicep deploy or migration run is
   duplicated.
4. **Revision pin → update → smoke → shift sequence.** With the
   API's Bicep traffic block gone, the workflow:
   * Reads the currently-100 %-weighted revision (`PREV`).
   * Pins traffic to `PREV` explicitly so the imminent revision
     creation does not auto-take traffic. On the very first deploy
     `PREV` is empty and this step is skipped.
   * Creates the new revision via `az containerapp update
     --revision-suffix sha-<short>`. With traffic pinned to `PREV`,
     the new revision starts at 0 % weight.
   * Smoke-tests the new revision through its unique
     `<app>--<suffix>.<env-defaultDomain>` FQDN, which routes
     specifically to that revision regardless of the app's traffic
     config.
   * On smoke green: shifts traffic with `NEW=100 PREV=0`. `PREV`
     stays warm for instant manual rollback.
5. **Rollback step is `if: failure() && ... && shift != success`.**
   GH Actions' `failure()` predicate fires on any prior-step failure
   in the job. The full condition narrows it further:
   * `api_update.conclusion == 'success'` — no revision to roll back
     from if the create itself failed.
   * `prev.outputs.name != ''` — no `PREV` to roll back to on the
     bootstrap deploy.
   * `shift.conclusion != 'success'` — if the shift already happened
     and a downstream step (worker update) failed, the API is on the
     new revision and working; leave it alone.
6. **Worker update without traffic shift.** ADR-0001 fixed the
   worker at `minReplicas = maxReplicas = 1`; ACA promotes the new
   revision once it is healthy and deactivates the old. The
   workflow's worker step is a single `az containerapp update
   --image ...:sha-<short> --revision-suffix sha-<short>` with no
   traffic management. Worker rollback (if needed) is documented in
   `docs/runbooks/deploy.md` and uses `az containerapp revision
   activate` / `deactivate` rather than weight shifts.
7. **SPA deploy: `npm ci && npm run build` → `az storage blob
   upload-batch --overwrite --auth-mode login` → `az afd endpoint
   purge`.** Only `/`, `/index.html`, and `/config.json` need
   explicit purges — the rest of the bundle is content-hashed by Vite
   and the URLs change per build, so the CDN misses on its own.
   `/config.json` is the runtime config (locked-in plan item 11) and
   is intentionally not hashed.
8. **Storage account name from Bicep outputs.** The storage name
   bakes in `uniqueString(resourceGroup().id)`, so the workflow can't
   hard-code it the way it can the API / worker / job names (those
   follow the deterministic `${workloadPrefix}-${environmentName}-X`
   pattern). The `prepare-infra` job captures
   `properties.outputs.storageAccountName.value` from the
   `az deployment group create` invocation and surfaces it as a job
   output for `deploy-spa`.

## Bicep tweak — drop `traffic[]` from container-app-api

The original module declared:

```bicep
ingress: {
  external: true
  targetPort: targetPort
  transport: 'auto'
  allowInsecure: false
  traffic: [
    {
      latestRevision: true
      weight: 100
    }
  ]
}
```

Every `az deployment group create` would re-apply this traffic
config. With `latestRevision: true`, any new revision automatically
takes 100 % traffic the instant it's created — the workflow's smoke
gate would never get a window to test the new code in isolation.
Solutions considered:

| Option | Why not |
|---|---|
| Keep the block; have `deploy.yml` only run `az containerapp update` (no Bicep) | Locked-in plan item 10 calls for the Bicep deploy on every push to keep infra current. Skipping defeats that intent. |
| Parameterise `traffic[]` and pass an empty array per deploy | Bicep arrays passed as parameters via `--parameters key=val` need JSON-escape gymnastics; brittle. |
| Switch to named-revision traffic with a `currentRevision` parameter | The workflow would need to know the existing revision's name before the Bicep deploy and pass it; couples Bicep and workflow tightly. |
| **Drop the property entirely.** ARM incremental mode preserves the live traffic state for properties not in the template. ACA defaults to `latestRevision: true, weight: 100` on the very first create, which is the correct bootstrap. | **Picked.** Smallest diff, no parameter shape changes, no workflow coupling. |

The new module ships with a long comment block at the ingress
section explaining the omission so the next person to edit it
doesn't put the block back. `az bicep build` is clean; the `unused
parameter` check doesn't fire because no parameter was added.

The worker module's `traffic[]` block stays — the worker has no
blue/green semantics and the platform default behaviour is what
we want.

## Decisions inline that warrant a paper trail

- **Concurrency group is per-environment, not per-SHA.** A second
  push to `main` in quick succession with `cancel-in-progress: false`
  queues behind the in-flight deploy rather than racing it. Aborting
  a mid-traffic-shift deploy would strand a revision at a partial
  weight, which is worse than waiting.
- **Migration polling timeout: 65 × 10 s = 650 s.** The migration
  Job's `replicaTimeout` is 600 s (WU6.4 acceptance criterion); the
  workflow polls for slightly longer to catch the wind-down message.
  A migration that legitimately runs past the Job's own timeout is
  already a failure case the workflow doesn't need to wait for.
- **Smoke-test polling: 60 × 5 s = 300 s.** ACA cold-start (image
  pull on first ever container start, /healthz probe lag) can spend
  30–60 s before the revision is reachable. A 5-minute budget is
  generous; if /healthz still isn't 2xx after 5 min, something is
  wrong and the rollback step needs to fire.
- **`curl --fail-with-body` rather than `--fail`.** `--fail-with-body`
  prints the response body on non-2xx; `--fail` discards it. For
  diagnostic logs in a CI run, the body is the most useful single
  artifact.
- **Tenant ID overridden via `--parameters tenantId="$AZURE_TENANT_ID"`.**
  `main.parameters.example.json` carries a placeholder all-zeros
  tenant ID; without the override the Bicep deployment would create
  Key Vault and the Postgres AAD admin against the zero tenant.
  Using `vars.AZURE_TENANT_ID` (provisioned by WU6.1) keeps the
  example file generic.
- **`POSTGRES_ADMIN_PASSWORD` is a `staging`-environment secret, not
  a repository secret.** It scopes to the GitHub Environment so a
  workflow running under a different environment (future
  `production`) can't accidentally read the staging password. The
  same key name in the prod environment carries the prod password.
- **`auth-mode login` for `az storage blob upload-batch`.** Uses the
  OIDC-federated UAMI's AAD token, not a storage account key. No
  storage key ever appears in workflow logs or env.
- **Workflow-injection defence.** Every `${{ }}` expression that
  lands inside a `run:` block is bound through a step-level `env:`
  declaration first, mirroring the pattern set by WU6.6. None of
  the current trigger sources (`workflow_run` SHA, `workflow_dispatch`
  choice input) provide attacker-controllable values, but the safer
  pattern stays correct if a future trigger introduces one.
- **`gh workflow run deploy.yml --field environment=staging`** is
  the recommended manual-dispatch command (documented in the runbook).
  Specifying `--field environment` rather than `--input` is the
  current `gh` CLI shape for non-string inputs; choice inputs
  accept either, but `--field` is more idiomatic.

## Things deliberately deferred

- **Auto-arm WU7.3 alerts after the first deploy.** The alert rules
  ship `enabled: false` so they don't fire "no data" notifications
  before the API + worker are populated. Arming is a manual ops
  step (`--parameters alertsEnabled=true` on a later
  `az deployment group create`) documented in the WU7.3 journal and
  re-stated in `docs/runbooks/deploy.md`. NOT done here per the
  prompt: "WU6.3 should NOT enable them automatically".
- **`az deployment group what-if` preview on deploy.** Drift
  detection is WU6.6's nightly job; running what-if on every deploy
  adds latency without clear signal at demo scale.
- **Production cutover.** The `production` GitHub Environment exists
  (WU6.1) with a federated credential, but no `horizons-prod`
  resource group, no `main.parameters.prod.json`, no required-
  reviewer rule, no production Postgres password secret, no AAD
  principal for the UAMI on a production Postgres server. The seven-
  step cutover sequence lives in
  `docs/runbooks/deploy.md#production-cutover-follow-up`.
- **Smoke test through Front Door endpoint (in addition to revision
  FQDN).** A post-shift smoke would catch ingress / DNS / TLS
  regressions; not wired. Future work.
- **SPA versioning / point-in-time rollback.** `$web` is overwritten
  in place. Rolling back the SPA requires a re-build at the previous
  SHA. Keeping the previous bundle under a versioned prefix is a
  future enhancement.
- **`cosign` signing + SBOM attestation.** The build-and-push
  workflow declares `id-token: write` already; adding signing is
  workflow-only diff. Out of scope for the demo.
- **Multi-revision soak / canary.** The current shift is 0 → 100 in
  one step. A 5 % canary with metric-driven promotion would require
  Track 7 alerts to be armed and a polling step in the workflow.
  Post-demo.

## Verification gate

```bash
$ python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "YAML OK"
YAML OK

$ az bicep build --file infra/main.bicep
# → exit 0; zero warnings

$ uv run ruff check .
# → All checks passed!

$ uv run pyright
# → 0 errors, 24 warnings (pre-existing testcontainers stubs)

$ uv run pytest -m "not integration" -q
# → 323 passed, 4 skipped, 189 deselected, 1 warning in 52.07s

$ uv run pre-commit run --all-files
# → every hook Passed
```

## External verification (user-only — flagged for post-merge)

The first real deploy run is the user's gate. Prerequisites:

1. **`POSTGRES_ADMIN_PASSWORD` secret on the `staging` GitHub
   Environment.** The example parameters file has a placeholder Key
   Vault reference; the workflow overrides with this secret. Without
   it, the Bicep deployment will fail at the postgresAdminPassword
   step.

2. **Static-website hosting flipped on the storage account.** One-off
   `az storage blob service-properties update --static-website
   --account-name <name> --index-document index.html --404-document
   index.html`. Without this, the SPA upload succeeds but the storage
   origin returns 404 on `/`. Documented in `infra/README.md`.

3. **ACA env bound to App Insights.** One-off `az containerapp env
   update --logs-destination log-analytics --logs-workspace-id <id>`.
   Without it, OTEL signals from the API and worker go to ACA's own
   log stream but not to App Insights, and WU7.3's alerts (when
   armed) sit in "Insufficient data". Documented in `infra/README.md`.

To trigger the first run:

```bash
# After ff-merging this branch to main and confirming the
# POSTGRES_ADMIN_PASSWORD secret is set on the staging environment:
gh workflow run deploy.yml --field environment=staging

# Watch:
gh run watch
# or:
gh run list --workflow=deploy.yml
```

A healthy first run shows (the full table is in
`docs/runbooks/deploy.md#what-a-healthy-run-looks-like`):

- `gate`: `head=<40-char>` then `short=<12-char>`.
- `prepare-infra`: Bicep deploy completes with a Storage account
  output; migration job status progresses `Running → Succeeded`
  within ~30–60 s for the demo's small migration tree; the
  workflow logs `Migration succeeded.`
- `deploy-services`: a `::warning::No 100%-traffic revision found;
  treating as bootstrap deploy.` line on the FIRST EVER deploy
  (subsequent deploys log `Previous active revision: ...`), then
  the create / smoke / shift / worker-update sequence.
- `deploy-spa`: npm install/build, an `az storage blob upload-batch`
  blob list, then a near-silent `az afd endpoint purge`.

Likely first-run failure modes:

- **`POSTGRES_ADMIN_PASSWORD` not set.** Bicep fails immediately at
  `parameter resolution` with a missing-secret error. Set the
  secret on the `staging` environment and re-run.
- **SPA upload 403 — missing `Storage Blob Data Contributor`.**
  WU6.1 granted `Contributor` on `horizons-nonprod` to the UAMI,
  which covers every control-plane call in the workflow but does
  NOT cover blob data-plane operations. The first SPA upload step
  (`az storage blob upload-batch --auth-mode login`) returns 403
  until the UAMI is granted `Storage Blob Data Contributor` on the
  storage account. Documented at the top of the
  `docs/runbooks/deploy.md` prerequisites table with the exact
  `az role assignment create` command. The API + worker deploy is
  unaffected — it's a control-plane chain. Caught during the WU6.3
  wrap-up code review; a follow-up could roll the role assignment
  into `infra/modules/storage.bicep` by accepting the UAMI principal
  ID as a parameter.
- **Migration Job's first run hits a fresh Postgres.** The Job logs
  show alembic creating the entire schema from scratch. If the
  Postgres `horizons` database itself doesn't exist, the
  postgres-flex Bicep module creates it on first deploy (database
  `horizons`); confirm via `az postgres flexible-server db list`.
- **Front Door endpoint purge before the SPA upload completes.** Not
  a race in the current shape — purge is a step **after** upload in
  the same job. Listed for completeness.
- **`az afd endpoint purge` 404.** The endpoint name is hard-coded
  to `horizons-dev` (matches `front-door.bicep`'s `endpointName =
  '${workloadPrefix}-${environmentName}'`). If the first deploy ran
  with different `workloadPrefix` / `environmentName` parameters,
  the purge step's endpoint name is stale.

## Pre-WU6.3 housekeeping flagged in prior journals

- **Required-reviewer rule on the `production` GitHub Environment**
  (WU6.1, WU6.6 journals). Recommended but not strictly required for
  WU6.3 because the `production` choice option is not yet wired into
  the workflow. Becomes mandatory at the production-cutover step in
  `docs/runbooks/deploy.md`.
- **AAD-user provisioning for the UAMI on Postgres** (WU6.4 journal).
  Until this lands, the migration Job uses the password fallback via
  `POSTGRES_ADMIN_PASSWORD`. Flipping to passwordless is a separate
  follow-up; the workflow does not depend on the order.

## Next pickup

Track 6 is complete after this WU:

- WU6.0 ✅ (Bicep skeletons)
- WU6.1 ✅ (OIDC federation)
- WU6.2 ✅ (Dockerfiles + GHCR)
- WU6.3 ✅ (deploy.yml + runbook + Bicep tweak) — **this WU**
- WU6.4 ✅ (migration ACA Job)
- WU6.5 ✅ (expand-contract policy)
- WU6.6 ✅ (drift-check workflow)

The Track 8 demo prep (curated set, demo accounts, smoke test, demo
runbook) is the natural next track once the API and webapp tracks
catch up. This session does NOT pick those up.
