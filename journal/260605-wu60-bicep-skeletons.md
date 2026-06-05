# WU6.0 — Bicep module skeletons

*Session 2026-06-05. Branch `worktree-wu6.0-6.2-infra-and-build` → ff-merged to `main`.*

The first chunk of Track 6 (IaC + CI/CD). No application code touched —
purely scaffolding for the Azure deployment shape that `deploy.yml`
(WU6.3) will exercise once it lands. `az bicep build` passes with zero
warnings; the `what-if` gate is **external** (requires the user's Azure
credentials) and is documented at the bottom of this entry as a
post-merge follow-up.

## What shipped

```
infra/
├── README.md                       Layout, decisions reflected, gates,
│                                   post-deploy one-off commands.
├── main.bicep                      Composes the nine modules at
│                                   targetScope=resourceGroup.
├── main.parameters.example.json    Hand-written example file with
│                                   placeholder tenant / subscription /
│                                   Key Vault references.
└── modules/
    ├── application-insights.bicep  Log Analytics + workspace-based AI.
    ├── container-app-api.bicep     External ingress; multi-revision.
    ├── container-app-env.bicep     ACA managed env, VNet-integrated.
    ├── container-app-worker.bicep  ADR-0001: long-running, 1 replica.
    ├── front-door.bicep            Standard SKU; origin = storage $web.
    ├── keyvault.bicep              RBAC-enabled; soft-delete; purge.
    ├── network.bicep               VNet + 2 delegated subnets + PG DNS.
    ├── postgres-flex.bicep         PG 17, VNet-integrated, AAD admin.
    └── storage.bicep               Originals container + $web.
```

Compiled ARM JSON is gitignored (`infra/main.json`, `infra/modules/*.json`).

## Architectural decisions reflected

Three locked-in plan items + one ADR drove the choices:

1. **ADR-0001 (worker shape).** `container-app-worker.bicep` is a
   `Microsoft.App/containerApps` resource with `minReplicas =
   maxReplicas = 1`, scale rules omitted, no external ingress, an
   internal `/healthz` liveness probe on port 8080. **Not** a
   `Microsoft.App/jobs`. Anyone tempted to swap to ACA Job needs to
   re-open ADR-0001 first.
2. **docs/4. services.md / locked-in plan §10.** API and worker are
   two separate `Microsoft.App/containerApps` resources sharing the
   same `managedEnvironments` parent. The skeleton makes co-locating
   them inconvenient (separate modules, separate parameter blocks) so
   the API-responsiveness invariant isn't accidentally violated.
   `activeRevisionsMode: Multiple` on both, ready for the WU6.3
   blue/green traffic-shift pattern.
3. **Locked-in plan §11 (SPA hosting).** `front-door.bicep` provisions
   Standard_AzureFrontDoor — **not** Azure CDN (managed certs expired
   April 2026). Origin host is the storage account's web endpoint with
   the `https://` prefix and trailing slash stripped.
4. **Locked-in plan §12 (observability).** Workspace-based App
   Insights bound to a Log Analytics workspace. The actual managed-OTEL
   binding to the ACA env is a control-plane action
   (`az containerapp env update --logs-destination …`), not a Bicep
   property — documented in `infra/README.md` and explicitly
   commented in `container-app-env.bicep` where the property used to
   live.

## Decisions inline that warrant a paper trail

- **No Azure Verified Modules in this skeleton.** The plan said "Use
  AVM where they exist; hand-roll where they don't." Hand-rolling
  every module keeps the build self-contained, no external module
  registry fetch at compile time, and every resource shape is visible
  in the diff. AVM swap is a follow-up if the modules accumulate too
  much boilerplate — flagged but not done.
- **No private endpoint for Storage / Key Vault.** Demo-scale call.
  Public access with `defaultAction: Allow` + AAD/RBAC is the
  baseline; a real customer cuts to private endpoints, which is a
  parameter flip, not a rewrite.
- **VNet address space `10.20.0.0/16` (`/24` pgsql, `/23` ACA).** No
  shared address space with the user's home network is assumed; both
  values override-able via `network.bicep` params.
- **PostgreSQL Burstable B1ms / 32 GB / no HA.** Demo sizing only.
  `passwordAuth` stays enabled for the demo (the WU1 role model uses
  password auth); `activeDirectoryAuth` is enabled in parallel so a
  prod cutover to passwordless is a parameter change.
- **Resource naming pattern.** `<workloadPrefix>-<envName>-<resource>`
  for every resource except the storage account (no hyphens allowed,
  no length > 24) and Key Vault (24-char cap → `uniqueString` suffix).
  `@minLength` / `@maxLength` constraints on the two prefixes feed the
  static analyzer enough information to prove the storage-name and
  KV-name bounds.

## Things deliberately deferred (NOT in this skeleton)

Each of these belongs to a later work unit. If you reach for any of
them while editing `infra/` later, re-read the plan first:

- **WU6.1 — OIDC federation.** `Microsoft.ManagedIdentity` user-assigned
  identities + federated credentials are a separate template provisioned
  out-of-band by the user. The deploy.yml pipeline assumes they exist.
- **WU6.3 — `deploy.yml`.** Revision-based blue/green and migration
  job orchestration land alongside the API code, not here.
- **WU6.4 — migration ACA Job.** A dedicated `Microsoft.App/jobs`
  resource running `alembic upgrade head` against the target DB. Stays
  out of `main.bicep` until WU3.1's migrations are stable across
  environments.
- **WU7.3 — alert rules.** Azure Monitor alert rules over App Insights
  metrics ship with Track 7.
- **Private endpoints for storage + KV.** Public-access defaults are
  acceptable for the demo; prod tightening is a parameter flip.

## Verification gate

```bash
uv run ruff check . && uv run pyright && uv run pytest -m "not integration" && uv run pre-commit run --all-files
# → ruff: All checks passed
# → pyright: 0 errors, 15 warnings (pre-existing testcontainers stubs)
# → pytest: 232 passed, 4 skipped, 102 deselected
# → pre-commit: every hook Passed
az bicep build --file infra/main.bicep
# → exit 0; zero warnings
```

## External verification (user-only — flagged for post-merge)

The acceptance criterion for WU6.0 includes `az deployment group
what-if`. That call needs the user's Azure credentials against a
non-prod subscription and was therefore NOT attempted in-session.

```bash
# Pre-requisites:
#   1. az login (interactive, user-only).
#   2. A non-prod subscription + resource group exist:
#        az group create --name horizons-dev-rg --location westeurope
#   3. A throwaway Postgres password is supplied for the run — the
#      example parameters file references a Key Vault secret; for a
#      first what-if just override with --parameters.

az deployment group what-if \
  --resource-group horizons-dev-rg \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword='REPLACE-ME-EPHEMERAL'
```

The `what-if` step must report a Create on every resource (nothing in
the RG yet). Watch for two pitfalls:

1. **VNet conflict.** If your non-prod RG already has a `10.20.0.0/16`
   VNet, `network.bicep`'s defaults will overlap. Pass
   `--parameters vnetAddressPrefix=10.30.0.0/16` (and matching subnet
   prefixes) to disambiguate.
2. **Key Vault name collision.** Vault names are tenant-globally
   unique. The `uniqueString(resourceGroup().id)` suffix should make
   them unique per RG, but if you re-run after a soft-delete the name
   stays purged for 90 days — pass a different `workloadPrefix` or
   wait out the purge window.

If `what-if` succeeds, the gate is met. If it reports any errors,
those are blockers for WU6.3 and need fixing before that work unit
opens.

## Lint warnings cleared in-session

First Bicep build raised four warnings. All cleared before commit:

| Warning | Module | Resolution |
|---|---|---|
| `BCP334` (storage name min length) | `storage.bicep` | Add `@minLength`/`@maxLength` to `workloadPrefix` + `environmentName`; rewrite `storageName` with statically-bounded interpolation. |
| `use-secure-value-for-secure-inputs` (×2) | `container-app-{api,worker}.bicep` | Drop the registry-credential ternary block; registry auth is set via `az containerapp secret set` post-deploy. |
| `outputs-should-not-contain-secrets` | `container-app-env.bicep` | Removed the diagnostic output that leaked `appInsightsConnectionString` through an `!empty()` check; also dropped the two parameters that were unused at compile time. |

Subsequent builds → zero warnings.

## Next pickup

WU6.1 (OIDC federation) and WU6.3 (deploy.yml) are the natural follow-ups.
WU6.1 requires user-only Azure portal work (federated credential
creation) and is therefore the user's task, not an agent's. WU6.3
depends on both WU4.4 (API endpoints stable) and WU6.1; pick it up
when both land.
