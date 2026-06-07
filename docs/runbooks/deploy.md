# Deploy runbook — staging pipeline (WU6.3)

*Last revised: 2026-06-06.*
*Path: docs/runbooks/deploy.md.*

_Audience: operator running or watching `.github/workflows/deploy.yml`. Companion to [migrations.md](./migrations.md),
which covers the expand-contract rule that keeps rollbacks safe._

## What the pipeline does

`.github/workflows/deploy.yml` runs on every successful `build-and-push.yml` (WU6.2) completion on `main`, and on a
manual `workflow_dispatch`. The pipeline targets the `staging` GitHub Environment, which is bound to the
`horizons-nonprod` resource group via the WU6.1 federated credential. Production is **not** wired yet — see "Production
cutover follow-up" below.

Three jobs run after a `gate` step that filters on the upstream conclusion and computes the build's short SHA:

```
gate ─┬──> prepare-infra (Bicep + migration ACA Job)
      ├──> deploy-services    (API revision update + worker update)   parallel
      └──> deploy-spa         (build, upload to $web, purge cache)    parallel
```

`deploy-services` and `deploy-spa` both depend only on `prepare-infra`; they do not block each other. A failed API deploy
does not stop the SPA from shipping the new bundle.

### prepare-infra

1. **Bicep deploy.** `az deployment group create` reconciles `infra/main.bicep` against the resource group. The image
   parameters are overridden with `:sha-<short>` so the migration Job (which reuses the API image) and any first-deploy
   provisioning use the immutable tag, not the moving `:latest`. Subsequent runs that only touch image tags are largely
   no-ops for existing resources.
2. **Migration ACA Job.** `az containerapp job start` invokes the `horizons-dev-migrate` Job (WU6.4), which runs
   `uv run alembic upgrade head`. The workflow polls `az containerapp job execution show` and fails fast on a `Failed` /
   `Stopped` / `Degraded` status. The API revision update below never runs against an un-migrated schema.

### deploy-services (API)

Both container apps run `activeRevisionsMode: Single`. The flow is the same for both, and minimal: a single
`az containerapp update --image ghcr.io/johnmathews/horizons-api:sha-<short> --revision-suffix sha-<short>` creates a new
revision named `horizons-dev-api--sha-<short>`, ACA waits for its `/healthz` readiness probe to come green (configured in
`infra/modules/container-app-api.bicep`: `periodSeconds: 5`, `failureThreshold: 3`), shifts 100 % of traffic to the new
revision, and deactivates the previous one. The `--revision-suffix` isn't required — ACA auto-generates a suffix when
omitted — but is kept for traceability: a `sha-<short>` in the revision name makes "which build is this?" a one-liner.

After the update returns, a smoke step curls the **stable** API FQDN (`<app>.<env-defaultDomain>`, not a per-revision
FQDN) and asserts `/healthz` 200 OK and `/openapi.json` reachable. This is a tripwire, not a gate: traffic has already
shifted by the time `az containerapp update` returned. If the smoke fails, the workflow fails, and the operator manually
re-deploys the previous SHA (see [Manual rollback](#manual-rollback)). The pre-shift smoke against a per-revision FQDN
that used to live here is gone — Single mode owns the shift, so there is no 0 %-weight window to test in.

### deploy-services (worker)

Same shape as the API: `az containerapp update --image ghcr.io/johnmathews/horizons-worker:sha-<short> --revision-suffix sha-<short>`.
The worker has one always-on replica per [ADR-0001](../adrs/0001-worker-shape.md) and no ingress, so there is no smoke
step — ACA's readiness probe is the only gate.

### deploy-spa

Runs in parallel with `deploy-services`:

1. `npm ci && npm run build` in `packages/horizons-webapp/`.
2. Rewrite `dist/config.json`'s `apiBaseUrl` to `https://<apiFqdn>` using `jq`. Vite copies `public/config.json` (dev
   default: `http://localhost:8000`) into `dist/` verbatim; without this step the deployed SPA would call localhost from
   an https origin and fail with a mixed-content "CORS request did not succeed" error. `apiFqdn` is read from the
   `prepare-infra` job's Bicep outputs; `tuningThresholds` + `featureFlags` are preserved from the bundled file.
3. `az storage blob upload-batch --overwrite --auth-mode login` to the storage account's `$web` container. The account
   name comes from the `prepare-infra` job's Bicep outputs.
4. `az afd endpoint purge` for `/`, `/index.html`, `/config.json`. The hashed asset URLs Vite emits don't need purging —
   their URLs change per build and the CDN misses on its own.

## Triggering a deploy

A push to `main` that triggers a successful `build-and-push.yml` fires this workflow automatically. To re-run a deploy
manually (e.g. after a transient failure):

```bash
gh workflow run deploy.yml --field environment=staging
```

Watch:

```bash
gh run watch
# or
gh run list --workflow=deploy.yml
```

### What a healthy run looks like

| Job               | Step                                 | Expected log line                                                                                        |
| ----------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `gate`            | Compute short SHA                    | `head=<40-char SHA>` followed by `short=<12-char prefix>`                                                |
| `prepare-infra`   | Bicep deploy                         | `Bicep deploy complete. Storage account: horizonsdevst<6chars>; API FQDN: horizons-dev-api.<env>.westeurope.azurecontainerapps.io` |
| `prepare-infra`   | Start migration ACA Job              | `[N/65] migration status: Succeeded` then `Migration succeeded.`                                         |
| `deploy-services` | Update API revision                  | The `az containerapp update` JSON response with `provisioningState: "Succeeded"`; then `Updated to revision: horizons-dev-api--sha-<short>`. |
| `deploy-services` | Smoke-test API (stable FQDN)         | `Smoke-testing https://horizons-dev-api.<env>.westeurope.azurecontainerapps.io` → `[N/12] /healthz OK` → `/openapi.json reachable`           |
| `deploy-services` | Update worker revision               | The `az containerapp update` JSON response with `provisioningState: "Succeeded"`                                                              |
| `deploy-spa`      | Inject production apiBaseUrl         | `dist/config.json rewritten:` followed by the JSON dump with `https://horizons-dev-api.<env>.westeurope.azurecontainerapps.io` |
| `deploy-spa`      | Upload SPA bundle to $web            | The blob list with the uploaded asset names                                                              |
| `deploy-spa`      | Purge Front Door cache               | `Successfully purged` (or empty success — `az afd endpoint purge` returns no JSON on success)            |

## Manual rollback

There is no automatic rollback. If a deploy completes but a regression surfaces (alert fires, the smoke step failed,
user report), roll back by re-deploying the previous image SHA.

Step 1 — find the previous SHA. The currently-active revision encodes the deployed SHA in its name
(`horizons-dev-api--sha-<short>`). The previous deployment's commit SHA is whatever sat at `HEAD~1` on `main` when the
current revision was built; `git log --oneline -5 main` is the fastest way to get it.

Step 2 — re-deploy. Manually trigger the deploy workflow against the previous build's commit, or, faster, run the
`az containerapp update` directly with the previous SHA:

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

ACA creates the rollback revision, waits for readiness, shifts traffic. ~3-5 min wall-clock per app. The image must
already be in GHCR — the rollback target is whatever the previous successful `build-and-push.yml` pushed, so any SHA that
ever made it to `main` is available.

### What rollback does NOT undo

- **Database migrations.** Alembic upgrades are not auto-reverted on a code rollback. The expand-contract policy in
  [migrations.md](./migrations.md) is what keeps the rolled-back code compatible with the migrated schema. If a migration
  broke things, see that runbook for the downgrade procedure.
- **SPA bundle.** The current pipeline overwrites `$web` in place with no versioning. Rolling back the SPA requires
  re-running the build at the previous SHA. Future work: keep the previous bundle under a versioned prefix so a `cp`
  between prefixes is the rollback.

### What we gave up

The earlier version of this pipeline ran `activeRevisionsMode: Multiple` on the API, kept PREV warm at 0 % weight, and
rolled back with a 5-second `az containerapp ingress traffic set` weight flip. We dropped that on 2026-06-06 (see
[`journal/260606-api-revisionmode-single.md`](../../journal/260606-api-revisionmode-single.md)): the wall-clock
difference (3-5 min vs. ~5s) doesn't pay for the maintenance cost of the traffic-shift + stale-revision-cleanup
machinery in `deploy.yml` at demo-scale. If we ever need sub-minute rollback for prod, revisit the decision then.

## Production cutover follow-up

The `production` GitHub Environment exists (WU6.1) with federated credentials, but no `horizons-prod` resource group is
provisioned and no production parameter file lives in `infra/`. Before the `production` environment can be used, the
following must land:

1. **Provision `horizons-prod` resource group** in the demo's chosen region.
2. **Write `infra/main.parameters.prod.json`** — same shape as `main.parameters.example.json` but with
   `environmentName: "prd"`, the production Key Vault reference for the Postgres password, and any production-specific
   overrides (CPU/memory sizing, etc.).
3. **Run `az deployment group what-if`** against `horizons-prod` with the new parameter file. Resolve any unexpected
   diffs before `create`.
4. **Add a required reviewer** to the `production` GitHub Environment (flagged in
   [`journal/260605-wu61-oidc-federation.md`](../../journal/260605-wu61-oidc-federation.md)) so a deploy to prod requires
   manual approval.
5. **Extend `deploy.yml`** — add a `production` option to the `workflow_dispatch` input choice list, and either
   parameterise the env block's hard-coded resource-group / app-name strings or branch on `inputs.environment` to switch
   them. Keep the workflow's `concurrency.group` partitioned by environment so a staging deploy never races a prod
   deploy.
6. **Set `secrets.POSTGRES_ADMIN_PASSWORD`** on the `production` Environment.
7. **Provision the production Postgres AAD principal** for the UAMI if cutting to passwordless at the same time (the SQL
   block lives in [`journal/260605-wu64-migration-aca-job.md`](../../journal/260605-wu64-migration-aca-job.md)).

After all seven, a `gh workflow run deploy.yml --field environment=production` reaches production behind the reviewer
gate.

## Prerequisites that must exist before the first deploy

The workflow assumes these are already in place:

| Item                                                                                                                                                                                                       | Where it lives                                                       | Set up by                                                                                                                                                                                                                                                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vars.AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`                                                                                                                                         | Repository variables                                                 | WU6.1                                                                                                                                                                                                                                                                                                        |
| Federated credential `repo:johnmathews/horizons:environment:staging` on UAMI `horizons-github-oidc`                                                                                                        | Azure portal                                                         | WU6.1                                                                                                                                                                                                                                                                                                        |
| `Contributor` role on `horizons-nonprod` for the UAMI's principal                                                                                                                                          | Azure RBAC                                                           | WU6.1 (control plane only — see next row)                                                                                                                                                                                                                                                                    |
| `Storage Blob Data Contributor` on the storage account for the UAMI's principal                                                                                                                            | Azure RBAC                                                           | **NEW — required for the SPA `upload-batch` step**                                                                                                                                                                                                                                                           |
| `Storage Blob Data Contributor` on the storage account for the worker's SystemAssigned identity (principal ID = `az containerapp show -n horizons-dev-worker -g <rg> --query identity.principalId -o tsv`) | Azure RBAC                                                           | First deploy follow-up — the worker creates the `originals` container on startup                                                                                                                                                                                                                             |
| `secrets.POSTGRES_ADMIN_PASSWORD`                                                                                                                                                                          | `staging` GitHub Environment secret                                  | **NEW — must be set before first deploy**                                                                                                                                                                                                                                                                    |
| `secrets.HORIZONS_JWT_PRIVATE_KEY_PEM` + `HORIZONS_JWT_PUBLIC_KEY_PEM`                                                                                                                                     | `staging` GitHub Environment secret                                  | RS256 keypair the API signs/verifies tokens with. Generate locally with `openssl genpkey -algorithm RSA -out priv.pem -pkeyopt rsa_keygen_bits:2048 && openssl rsa -pubout -in priv.pem -out pub.pem`, then `gh secret set HORIZONS_JWT_PRIVATE_KEY_PEM --env staging < priv.pem` (same for the public key). |
| GHCR packages `horizons-api`, `horizons-worker` set to public visibility                                                                                                                                   | github.com package settings                                          | WU6.2 (post-first-push flip)                                                                                                                                                                                                                                                                                 |
| Static-website hosting enabled on the storage account                                                                                                                                                      | One-off `az storage blob service-properties update --static-website` | First deploy follow-up — see `infra/README.md`                                                                                                                                                                                                                                                               |
| ACA env bound to App Insights via `az containerapp env update --logs-destination log-analytics`                                                                                                            | One-off control-plane action                                         | First deploy follow-up — see `infra/README.md`                                                                                                                                                                                                                                                               |
| Postgres Flexible Server `horizons-dev-pgsql`                                                                                                                                                              | Manual `Deploy Postgres` workflow (`deploy-postgres.yml`)            | Split out of routine deploy — see below                                                                                                                                                                                                                                                                      |

`Contributor` is a control-plane role — it grants permission to create / modify / delete resources but **not** to read or
write blob data. The SPA upload step uses `--auth-mode login` against the storage account's blob endpoint, which is a
data-plane operation requiring a separate role assignment. After the first Bicep deploy creates the storage account,
grant the role with:

```bash
PRINCIPAL_ID=$(az identity show \
  --resource-group horizons-nonprod \
  --name horizons-github-oidc \
  --query principalId -o tsv)
STORAGE_ID=$(az storage account list \
  --resource-group horizons-nonprod \
  --query "[0].id" -o tsv)
az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$STORAGE_ID"
```

Then re-run the deploy (`gh workflow run deploy.yml --field environment=staging`) — the SPA upload step succeeds on the
retry.

The two "one-off" rows are idempotent and can be re-run safely. The storage-website flip is what makes the SPA reachable
through Front Door at all — without it, the upload succeeds but `/` returns 404 from the storage origin.

### Postgres lives in its own deploy

The Flexible Server is deployed by a separate manual workflow, `.github/workflows/deploy-postgres.yml`, against
`infra/postgres.bicep`. The routine `deploy.yml` reads the server FQDN via an `existing` lookup in `infra/main.bicep` and
never asserts the server resource. Reason: re-asserting the server (in particular the `@secure()` admin password) on
every push held a control-plane lock for ~5 min after each write, and the next push collided with it, producing repeated
`ServerIsBusy` failures.

Run the Postgres deploy when you actually need to change the server — version bump, SKU change, storage scale, password
rotation:

```bash
gh workflow run deploy-postgres.yml \
  --field environment=staging \
  --field confirm=DEPLOY
```

The `confirm=DEPLOY` literal is a guard against accidental dispatch. The workflow refuses to run without it.

If the server doesn't exist yet (fresh resource group), run `Deploy Postgres` once before the first `Deploy` run —
`main.bicep`'s `existing` lookup fails compile-time if the server is absent.

## Things deliberately NOT in this pipeline

- **`az deployment group what-if` preview.** Drift detection is WU6.6's nightly job; on-deploy what-if would add latency
  to every push without clear signal for the demo.
- **SBOM / signature attestation.** Build-and-push leaves placeholders (`id-token: write` is declared but unused for
  signing). Post-demo, add `cosign sign` and SBOM generation.
- **Auto-enable WU7.3 alerts.** The alert rules ship `enabled: false` so they don't fire "no data" before traffic exists.
  Arming them is a manual ops step documented in the WU7.3 journal — flip `--parameters alertsEnabled=true` on a later
  `az deployment group create` once `AppRequests` / `AppTraces` rows are flowing.
- **Smoke test through Front Door.** The current smoke hits the stable API FQDN directly (skipping Front Door). A
  separate smoke through the Front Door endpoint would catch ingress / DNS / TLS issues end-to-end; not yet wired.
- **SPA versioning / rollback.** `$web` is overwritten in place per the trade-off above; previous bundles are not
  retained.
