# Deploy runbook — staging pipeline (WU6.3)

*Audience: operator running or watching `.github/workflows/deploy.yml`.
Companion to [migrations.md](./migrations.md), which covers the
expand-contract rule that keeps blue/green rollbacks safe.*

## What the pipeline does

`.github/workflows/deploy.yml` runs on every successful
`build-and-push.yml` (WU6.2) completion on `main`, and on a manual
`workflow_dispatch`. The pipeline targets the `staging` GitHub
Environment, which is bound to the `horizons-nonprod` resource group
via the WU6.1 federated credential. Production is **not** wired
yet — see "Production cutover follow-up" below.

Three jobs run after a `gate` step that filters on the upstream
conclusion and computes the build's short SHA:

```
gate ─┬──> prepare-infra (Bicep + migration ACA Job)
      ├──> deploy-services    (API blue/green + worker update)   parallel
      └──> deploy-spa         (build, upload to $web, purge cache) parallel
```

`deploy-services` and `deploy-spa` both depend only on
`prepare-infra`; they do not block each other. A failed API deploy
does not stop the SPA from shipping the new bundle.

### prepare-infra

1. **Bicep deploy.** `az deployment group create` reconciles
   `infra/main.bicep` against the resource group. The image
   parameters are overridden with `:sha-<short>` so the migration Job
   (which reuses the API image) and any first-deploy provisioning use
   the immutable tag, not the moving `:latest`. Subsequent runs that
   only touch image tags are largely no-ops for existing resources.
2. **Migration ACA Job.** `az containerapp job start` invokes the
   `horizons-dev-migrate` Job (WU6.4), which runs
   `uv run alembic upgrade head`. The workflow polls
   `az containerapp job execution show` and fails fast on a
   `Failed` / `Stopped` / `Degraded` status. The traffic shift below
   never runs against an un-migrated schema.

### deploy-services (API blue/green)

The API container app's Bicep deliberately omits the `traffic[]`
block — see `infra/modules/container-app-api.bicep` — so traffic is
managed imperatively here. The sequence:

1. **Capture PREV.** Query the revision list for the revision
   currently at 100 % weight. On the very first deploy this is empty
   and the rest of the dance simplifies to a "shift 100 % to NEW"
   step.
2. **Pin PREV.** `az containerapp ingress traffic set --revision-weight PREV=100`
   converts the (possibly platform-default) `latestRevision: true`
   traffic config into an explicit named-revision config, so the
   imminent revision creation does **not** auto-take traffic.
3. **Create NEW at 0 %.** `az containerapp update --image
   ghcr.io/johnmathews/horizons-api:sha-<short> --revision-suffix
   sha-<short>` creates revision `horizons-dev-api--sha-<short>`.
   PREV continues to serve 100 % of traffic.
4. **Smoke-test NEW.** Each revision has its own FQDN of the form
   `<app>--<suffix>.<env-defaultDomain>`. The smoke step polls
   `/healthz` until 200 OK (up to ~5 min cold-start budget) then
   asserts both `/healthz` and `/openapi.json` respond. `curl
   --fail-with-body` surfaces the response body in the workflow log
   on non-2xx.
5. **Shift to NEW.** `az containerapp ingress traffic set
   --revision-weight NEW=100 PREV=0`. NEW now serves 100 %; PREV
   stays warm (active, 0 % weight) for instant rollback.
6. **Rollback on failure.** If any step between (3) and (5) fails,
   an `if: failure()` step shifts traffic back to PREV with NEW at
   0 % and fails the workflow. The trigger condition explicitly
   excludes the case where shift (5) succeeded, so a downstream
   worker-update failure does not undo a healthy API deploy.

### deploy-services (worker)

After the API traffic shift, the worker is updated with the same
`--revision-suffix sha-<short>` pattern but no traffic management.
The worker has one always-on replica per
[ADR-0001](../adrs/0001-worker-shape.md); ACA promotes the new
revision once it is healthy and deactivates the previous one.

### deploy-spa

Runs in parallel with `deploy-services`:

1. `npm ci && npm run build` in `packages/horizons-webapp/`.
2. `az storage blob upload-batch --overwrite --auth-mode login` to
   the storage account's `$web` container. The account name comes
   from the `prepare-infra` job's Bicep outputs.
3. `az afd endpoint purge` for `/`, `/index.html`, `/config.json`.
   The hashed asset URLs Vite emits don't need purging — their URLs
   change per build and the CDN misses on its own.

## Triggering a deploy

A push to `main` that triggers a successful `build-and-push.yml`
fires this workflow automatically. To re-run a deploy manually
(e.g. after a transient failure):

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

| Job | Step | Expected log line |
|---|---|---|
| `gate` | Compute short SHA | `head=<40-char SHA>` followed by `short=<12-char prefix>` |
| `prepare-infra` | Bicep deploy | `Bicep deploy complete. Storage account: horizonsdevst<6chars>` |
| `prepare-infra` | Start migration ACA Job | `[N/65] migration status: Succeeded` then `Migration succeeded.` |
| `deploy-services` | Capture previous active API revision | `Previous active revision: horizons-dev-api--<suffix>` (or `bootstrap deploy` warning on first ever run) |
| `deploy-services` | Create new API revision (0% traffic) | `Created revision: horizons-dev-api--sha-<short>` |
| `deploy-services` | Smoke test new revision | `Smoke-testing https://<rev-fqdn>` → `[N/60] /healthz OK` → `/openapi.json reachable` |
| `deploy-services` | Shift traffic to new revision | `Shifted: <new>=100, <prev>=0` |
| `deploy-services` | Update worker revision | The `az containerapp update` JSON response with `provisioningState: "Succeeded"` |
| `deploy-spa` | Upload SPA bundle to $web | The blob list with the uploaded asset names |
| `deploy-spa` | Purge Front Door cache | `Successfully purged` (or empty success — `az afd endpoint purge` returns no JSON on success) |

## Manual rollback

The pipeline's automatic rollback only fires while the workflow is
running. If a deploy completes green but an issue surfaces later
(an alert fires, a regression report comes in), roll back by hand.

PREV is always the previous revision name — `az containerapp
revision list` returns it sorted by creation time. For a single-hop
rollback:

```bash
# Find the two most recent revisions, newest first.
az containerapp revision list \
  --name horizons-dev-api \
  --resource-group horizons-nonprod \
  --query "[].{name:name, weight:properties.trafficWeight, active:properties.active, created:properties.createdTime}" \
  -o table | head -5

# Reactivate the previous revision if it has been deactivated (after
# a while, idle revisions get deactivated automatically to free
# replicas).
az containerapp revision activate \
  --name horizons-dev-api \
  --resource-group horizons-nonprod \
  --revision <previous-revision-name>

# Flip traffic back. 100% to the previous revision, 0% to the
# currently-active one.
az containerapp ingress traffic set \
  --name horizons-dev-api \
  --resource-group horizons-nonprod \
  --revision-weight <previous-revision-name>=100 <current-revision-name>=0
```

The shift takes effect within a few seconds — ACA's load balancer
re-routes immediately.

### What rollback does NOT undo

* **Database migrations.** Alembic upgrades are not auto-reverted on
  a code rollback. The expand-contract policy in
  [migrations.md](./migrations.md) is what keeps the rolled-back code
  compatible with the migrated schema. If a migration broke things,
  see that runbook for the downgrade procedure.
* **Worker container.** A worker rollback uses the same
  `revision activate` + (no traffic step — single replica)
  pattern; the worker has no `--revision-weight` semantics.

  ```bash
  az containerapp revision activate \
    --name horizons-dev-worker \
    --resource-group horizons-nonprod \
    --revision <previous-revision-name>
  az containerapp revision deactivate \
    --name horizons-dev-worker \
    --resource-group horizons-nonprod \
    --revision <current-revision-name>
  ```

* **SPA bundle.** The current pipeline overwrites `$web` in place
  with no versioning. Rolling back the SPA requires re-running the
  build at the previous SHA. Future work: keep the previous bundle
  under a versioned prefix so a `cp` between prefixes is the rollback.

## Production cutover follow-up

The `production` GitHub Environment exists (WU6.1) with federated
credentials, but no `horizons-prod` resource group is provisioned and
no production parameter file lives in `infra/`. Before the
`production` environment can be used, the following must land:

1. **Provision `horizons-prod` resource group** in the demo's chosen
   region.
2. **Write `infra/main.parameters.prod.json`** — same shape as
   `main.parameters.example.json` but with `environmentName: "prd"`,
   the production Key Vault reference for the Postgres password, and
   any production-specific overrides (CPU/memory sizing, etc.).
3. **Run `az deployment group what-if`** against `horizons-prod`
   with the new parameter file. Resolve any unexpected diffs before
   `create`.
4. **Add a required reviewer** to the `production` GitHub Environment
   (flagged in [`journal/260605-wu61-oidc-federation.md`](../../journal/260605-wu61-oidc-federation.md))
   so a deploy to prod requires manual approval.
5. **Extend `deploy.yml`** — add a `production` option to the
   `workflow_dispatch` input choice list, and either parameterise the
   env block's hard-coded resource-group / app-name strings or branch
   on `inputs.environment` to switch them. Keep the workflow's
   `concurrency.group` partitioned by environment so a staging deploy
   never races a prod deploy.
6. **Set `secrets.POSTGRES_ADMIN_PASSWORD`** on the `production`
   Environment.
7. **Provision the production Postgres AAD principal** for the UAMI
   if cutting to passwordless at the same time (the SQL block lives
   in [`journal/260605-wu64-migration-aca-job.md`](../../journal/260605-wu64-migration-aca-job.md)).

After all seven, a `gh workflow run deploy.yml --field
environment=production` reaches production behind the reviewer gate.

## Prerequisites that must exist before the first deploy

The workflow assumes these are already in place:

| Item | Where it lives | Set up by |
|---|---|---|
| `vars.AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Repository variables | WU6.1 |
| Federated credential `repo:johnmathews/horizons:environment:staging` on UAMI `horizons-github-oidc` | Azure portal | WU6.1 |
| `Contributor` role on `horizons-nonprod` for the UAMI's principal | Azure RBAC | WU6.1 |
| `secrets.POSTGRES_ADMIN_PASSWORD` | `staging` GitHub Environment secret | **NEW — must be set before first deploy** |
| GHCR packages `horizons-api`, `horizons-worker` set to public visibility | github.com package settings | WU6.2 (post-first-push flip) |
| Static-website hosting enabled on the storage account | One-off `az storage blob service-properties update --static-website` | First deploy follow-up — see `infra/README.md` |
| ACA env bound to App Insights via `az containerapp env update --logs-destination log-analytics` | One-off control-plane action | First deploy follow-up — see `infra/README.md` |

The two "one-off" rows are idempotent and can be re-run safely. The
storage-website flip is what makes the SPA reachable through Front
Door at all — without it, the upload succeeds but `/` returns 404
from the storage origin.

## Things deliberately NOT in this pipeline

* **`az deployment group what-if` preview.** Drift detection is
  WU6.6's nightly job; on-deploy what-if would add latency to every
  push without clear signal for the demo.
* **SBOM / signature attestation.** Build-and-push leaves
  placeholders (`id-token: write` is declared but unused for
  signing). Post-demo, add `cosign sign` and SBOM generation.
* **Auto-enable WU7.3 alerts.** The alert rules ship `enabled:
  false` so they don't fire "no data" before traffic exists. Arming
  them is a manual ops step documented in the WU7.3 journal — flip
  `--parameters alertsEnabled=true` on a later `az deployment group
  create` once `AppRequests` / `AppTraces` rows are flowing.
* **Smoke test through Front Door (vs. the revision FQDN).** Hitting
  the revision FQDN bypasses traffic weighting and confirms the new
  code itself is healthy. A separate "post-shift" smoke through the
  Front Door endpoint would catch ingress / DNS / TLS issues; not
  yet wired.
* **SPA versioning / rollback.** `$web` is overwritten in place per
  the trade-off above; previous bundles are not retained.
