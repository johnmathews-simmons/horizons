# `infra/` — Bicep modules

*Last revised: 2026-06-06.*
*Path: infra/README.md.*

Skeleton Bicep templates for the Horizons demo. Composed by `main.bicep`
into one resource-group deployment.

## Layout

```
infra/
├── README.md
├── main.bicep
├── main.parameters.example.json
└── modules/
    ├── alerts.bicep                  Action Group + 3 scheduledQueryRules (WU7.3)
    ├── application-insights.bicep    Workspace + App Insights
    ├── container-app-api.bicep       Public REST API (external ingress)
    ├── container-app-env.bicep       ACA environment, OTEL bound
    ├── container-app-worker.bicep    Ingestion worker (ADR-0001 shape)
    ├── front-door.bicep              Standard SKU, fronts storage $web
    ├── keyvault.bicep                RBAC-enabled vault
    ├── network.bicep                 VNet + delegated subnets + PG DNS zone
    ├── postgres-flex.bicep           Flexible Server PG 17, VNet-integrated
    └── storage.bicep                 Originals container + $web
```

## Architectural decisions reflected here

- **Worker shape (ADR-0001):** `container-app-worker.bicep` is a
  `Microsoft.App/containerApps` resource with `minReplicas = maxReplicas = 1`
  and an internal `/healthz` probe — **not** a `Microsoft.App/jobs`. If you
  reach for `Microsoft.App/jobs`, you are arguing with the ADR; re-open the
  decision there first.
- **API ≠ worker (docs/RFC-4 services.md):** They are two separate
  `containerApps` resources inside the same `managedEnvironments`. They
  must not be co-located; an ingestion burst cannot starve the API of
  CPU.
- **Revision mode (locked-in plan §10, revised 2026-06-06):** Both
  container apps run `activeRevisionsMode: Single`. ACA owns the
  revision shift and previous-revision deactivation on every update.
  Rollback is `az containerapp update --image :sha-PREV`; see
  `docs/runbooks/deploy.md`. The original plan called for `Multiple`
  to support traffic-weight blue/green; we walked that back — the
  maintenance cost in `deploy.yml` wasn't worth the 5-second-vs-
  5-minute rollback delta at demo scale.
- **SPA hosting (locked-in plan §11):** Storage `$web` + Azure Front
  Door Standard. **Not** Azure CDN — its managed certs expired April
  2026.
- **Observability (locked-in plan §12):** Workspace-based App Insights
  bound to the ACA managed OTEL agent.
- **Alerts (WU7.3):** Three `scheduledQueryRules` (5xx ratio, p95 latency,
  ingestion failures) routed through a single Action Group with one
  email receiver. All three ship with `alertsEnabled: false`; arm
  per-environment after WU6.3 deploys the API + worker. See
  `journal/260605-wu73-alert-rules.md` for the enable commands.

## Verification

```bash
# Syntactic + lint check.
az bicep build --file infra/main.bicep

# What-if against a non-prod RG (requires Azure credentials; see
# journal/<wu60 entry> for the exact command).
az deployment group what-if \
  --resource-group <rg> \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword=<temporary>
```

The `what-if` step is **external verification**: it requires the user's
Azure credentials and a non-prod subscription, and is therefore deferred
to the post-merge follow-up. The build step runs in the local sweep and
in CI on every push.

## Post-deployment one-off steps (out of band)

Some toggles aren't exposed in the Bicep schema and must be flipped after
the deployment completes. These are idempotent and safe to re-run.

```bash
# Enable static-website hosting on the storage account.
# (The Bicep declares the $web container; this flips the feature.)
az storage blob service-properties update \
  --account-name <storageAccountName output> \
  --static-website \
  --index-document index.html \
  --404-document index.html

# Bind the ACA managed environment to App Insights for OTEL.
# (The Bicep parameter is forwarded but the binding is a control-plane
# action.)
az containerapp env update \
  --name <containerEnvName output> \
  --resource-group <rg> \
  --logs-destination log-analytics \
  --logs-workspace-id <workspaceCustomerId>
```

These two commands belong in `docs/runbooks/deploy.md` once WU6.3
lands; for now, they live here.

## Things NOT in this skeleton (intentional deferrals)

The following are the responsibility of later work units. Re-read the
improvement plan before adding any of them here:

- **OIDC federation (WU6.1).** The Microsoft.ManagedIdentity resources
  and federated credentials for GitHub → Azure live in a separate Bicep
  template that the user provisions once, manually.
- **Migrations ACA Job (WU6.4).** The schema-migration runner is its
  own resource and gets added by WU6.4, after WU3.1 has tables to
  migrate.
- **Drift check (WU6.6).** A separate workflow runs `what-if`
  nightly; the Bicep itself doesn't change.
