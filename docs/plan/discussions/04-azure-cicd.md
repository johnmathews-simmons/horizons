# Azure deployment & CI/CD plan — Horizons

*Last revised: 2026-06-06.*
*Path: docs/plan/discussions/04-azure-cicd.md.*

*Senior DevOps review, 2026-06-04. Pre-code, post-design. Demo target: 2026-06-08.*

Each item is tagged **[VERIFIED]** (load-bearing claim cross-checked against the cited doc) or **[SUSPECTED]** (judgement call, design preference, or extrapolation from current docs that I have not run end-to-end). Each recommendation states the **choice**, the **runner-up**, and the **why**.

---

## A. IaC choice

### 1. Bicep vs Terraform — recommend **Bicep**

- **Choice:** Bicep. **Runner-up:** Terraform (`hashicorp/azurerm` + the AVM Container App module).
- **Why:** Azure-only stack, no multi-cloud ambition. A 2026 comparison concludes "For Azure-only, small-to-mid team scenarios with no multi-cloud ambition, Bicep is simpler, cheaper to operate, and ergonomically tight with Azure" ([Bicep vs Terraform 2026](https://technspire.com/en/blog/bicep-vs-terraform-azure-2026-honest-update)). **[VERIFIED]**
- Three concrete benefits:
  1. **Day-zero coverage of new Azure features.** The AzureRM provider has historically lagged ACA features by weeks ([Microsoft Learn: Comparing Terraform and Bicep](https://learn.microsoft.com/en-us/azure/developer/terraform/comparing-terraform-and-bicep)). ACA's API surface is still moving (deployment labels, blue/green primitives). **[VERIFIED]**
  2. **No state file.** Bicep talks directly to ARM; Azure's deployment history is the audit trail. One fewer secured artefact, one fewer locking story. **[VERIFIED]**
  3. **Single team, single cloud, demo-grade scope.** Terraform's ecosystem advantage doesn't pay off here. **[SUSPECTED]**
- **Caveat:** If a later phase needs Cloudflare / GitHub-repo / Datadog config alongside Azure, revisit. Swap cost is moderate. **[SUSPECTED]**

### 2. IaC layout — `infra/` in the same repo

- **Choice:** `infra/` in this monorepo, beside `services/api`, `services/worker`, `webapp/`. Submodules: `infra/modules/{container-app,postgres,storage,keyvault}/`, environments at `infra/envs/{dev,demo,prod}/main.bicep`. **[SUSPECTED]**
- **Runner-up:** Separate `horizons-infra` repo. Rejected: it forces two PRs for any code+infra change (env var rename, new secret, scale tweak).
- **Why:** PRs that touch app and infra together get reviewed together. Pre-code phase makes co-location cheap. Add `docs/infra.md` to satisfy the global `/docs` rule.

### 3. State management

- **Bicep has no state.** Each deploy is `az deployment group create` — idempotent against ARM. Deployment history is queryable via `az deployment group list` and the Portal Activity Log for 90 days. **[VERIFIED]**
- **Operational story:** `what-if` first, then apply. CI posts `what-if` output as a PR comment on infra PRs. Resource group is the isolation unit (`rg-horizons-demo`, `rg-horizons-prod`). Drift detection = nightly `what-if`. **[SUSPECTED]**
- **Decommissioning:** `az group delete --name rg-horizons-demo --yes`. One command.

---

## B. ACA topology

### 4. Container Apps environment & app structure

- **Choice:** One ACA environment per logical env (`cae-horizons-demo`, later `cae-horizons-prod`). Two Container Apps inside each: `ca-horizons-api`, `ca-horizons-worker`. SPA is not a Container App (see §17). **[SUSPECTED]**
- **Runner-up:** Single environment, prod/demo segregated by revision. Rejected: blast radius too wide; one env outage takes both down.
- **Why:** The environment is the security/networking/observability boundary — Log Analytics workspace, VNet, Dapr all attach there ([Microsoft Learn: Container Apps environments](https://learn.microsoft.com/en-us/azure/container-apps/environment)). Separating demo gives a clean "nuke and rebuild" rollback target. **[VERIFIED]**
- **Revision suffix:** `sha-${GITHUB_SHA::7}` — readable in portal, traceable to commit.

### 5. Ingress

- **API:** `external: true`, `targetPort: 8000`, `transport: auto`, `allowInsecure: false`. ACA terminates TLS; custom domain bound later. **[VERIFIED]**
- **Worker:** No ingress (`ingress: null`). Per doc 4, only its scheduler and Postgres contact it. **[VERIFIED]**
- **Why not internal ingress?** Same cost as none, and the worker has nothing to expose.

### 6. Scaling rules

- **API:** HTTP scaler, `concurrency: 50` per replica. min=1 (no scale-to-zero during demo — see §22), max=5 demo / max=20 prod. Concurrency over RPS because ACA's HTTP scaler reads concurrent requests via envoy directly; RPS would need a custom KEDA rule. ([Microsoft Learn: Scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)) **[VERIFIED]**
- **Worker (if container, not job):** KEDA Postgres scaler querying `SELECT count(*) FROM document_poll_schedule WHERE next_poll_at <= now()` with `targetQueryValue: 10`; min=0, max=3. ([KEDA Postgres scaler](https://keda.sh/docs/2.13/scalers/postgresql/)) **[VERIFIED]** — but §8 makes this moot.

### 7. Resource sizing (starter)

| Service | CPU | Memory | Replicas (min/max) |
|---|---|---|---|
| API (demo) | 0.5 vCPU | 1.0 GiB | 1 / 5 |
| API (prod) | 1.0 vCPU | 2.0 GiB | 2 / 20 |
| Worker (Job) | 1.0 vCPU | 2.0 GiB | parallelism 1 |
| Worker (App, alt) | 0.5 vCPU | 1.0 GiB | 0 / 3 |

[SUSPECTED] — ACA's 0.25 vCPU / 0.5 GiB floor is too tight for a Python app with pooling + JWT + JSON serialisation under load. Re-baseline after a load test.

### 8. Worker shape — **ACA Job (scheduled)**

- **Choice:** ACA Job `cj-horizons-ingest`, cron `*/15 * * * *`, `parallelism: 1`, `replicaTimeout: 600s`. Reads `document_poll_schedule` with `SELECT ... FOR UPDATE SKIP LOCKED` per doc 4 §"Ingestion / How", commits per-document, exits.
- **Runner-up:** Long-running container with internal scheduler. Rejected:
  - Polling is intrinsically cron-shaped; doc 4 says "scheduled worker".
  - Jobs have no ingress, no idle cost, no cold-start tradeoff. ([Microsoft Learn: Jobs in ACA](https://learn.microsoft.com/en-us/azure/container-apps/jobs)) **[VERIFIED]**
  - Per-execution logs are first-class in the portal; failed executions map cleanly to `ingestion_incident` rows from doc 4.
- **Caveat:** If event-driven ingestion (Lawstronaut webhook) appears later, swap to a long-running container with KEDA HTTP scaler.

---

## C. Secrets & config

### 9. Key Vault + user-assigned managed identity

- **Choice:** Azure Key Vault as the store; user-assigned managed identity (UAMI) shared between API and worker; ACA references Key Vault secrets natively. ([Microsoft Learn: Manage secrets in ACA](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)) **[VERIFIED]**
- **Runner-up:** ACA secrets only. Rejected: no rotation story without a redeploy, no centralised audit, no cross-app sharing.
- **Layering:**
  1. Secret in `kv-horizons-{env}`.
  2. UAMI `id-horizons-app` with RBAC role **Key Vault Secrets User**.
  3. UAMI attached to both Container Apps and the Job.
  4. ACA secret block: `keyVaultUrl: https://kv-horizons-demo.vault.azure.net/secrets/<name>, identity: <UAMI id>`. **[VERIFIED]**
  5. Container reads via `env.secretRef`. App code never calls the Key Vault SDK directly — keeps secrets out of the hot path and rotation-safe.
- **Why UAMI over SAMI:** UAMI survives Container App recreation; SAMI's RBAC binding dies with the app, which makes IaC reapply painful.

### 10. Postgres — **passwordless via managed identity**

- **Choice:** Entra/managed-identity passwordless to Flexible Server. API and worker auth as the UAMI; Postgres role mapping grants `client` and `worker` to that identity (or two UAMIs for attribution).
- **Why:** Per [Microsoft Learn: Connect with Managed Identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity), Flexible Server accepts Entra access tokens in the password field. No rotation, no secret in Key Vault for the DB, no static credential ever leaves Azure. The multi-tenant defence-in-depth story (doc 4) is strengthened — leaked container env ≠ leaked DB credential. **[VERIFIED]**
- **Runner-up:** Connection string in Key Vault. Rejected: just moves the rotation problem.
- **Note:** App must refresh tokens (~24h TTL). Use `DefaultAzureCredential` with a token cache; `psycopg`/`asyncpg` accept per-connection password callbacks.

### 11. Rotation — Lawstronaut creds & JWT signing key

- **Lawstronaut creds:** two Key Vault secrets, worker-only. Versioned secret; ACA reference resolves latest on revision creation. Quarterly manual rotation. **[SUSPECTED]**
- **JWT signing key:** Key Vault key (HSM-backed if budget allows). Either signed via Key Vault endpoint or materialised at startup. **Rotation:** dual-key window — API accepts previous-or-current for 24h; next deploy drops previous.
- Document rotation procedures in `docs/operations/rotation.md`.

---

## D. CI/CD pipeline

### 12. `.github/workflows/` layout

Four workflows.

- **`ci.yml`** — on `pull_request`: matrix over `services/api`, `services/worker`, `webapp/`. Steps: `uv sync` → `ruff check` → `mypy` → `pytest --cov`. Path filters keep unaffected services out of the run.
- **`build-and-push.yml`** — on push to `main`: build all three images, tag `:sha-${GITHUB_SHA::7}` and `:latest` (latest informational; deploys use the SHA). Push to `ghcr.io/johnmathews/horizons-{api,worker}`; SPA bundle uploaded as artefact. Image digests exposed as workflow outputs.
- **`deploy.yml`** — `workflow_run` on success of build, branch=main:
  - Azure login via OIDC federated credential (§13).
  - `az deployment group what-if` against `infra/envs/demo/main.bicep` — fail on drift.
  - Run migration job (§15).
  - `az containerapp update --image ...:sha-XXXX --revision-suffix sha-XXXX` for API; same for worker.
  - **Smoke tests** against the new revision's FQDN (ACA gives each revision a hostname). ([Microsoft Learn: Update and deploy changes](https://learn.microsoft.com/en-us/azure/container-apps/revisions)) **[VERIFIED]**
  - On pass: `az containerapp ingress traffic set --revision-weight latest=100`. On fail: leave traffic on previous, alert, exit non-zero.
  - SPA: `az storage blob upload-batch` into `$web` + Front Door purge.
- **`drift-check.yml`** — nightly cron: `what-if` against demo; alert if non-empty.

### 13. GHCR → ACA pull authentication

- **Choice (now):** GitHub PAT (classic, `read:packages` only) stored in Key Vault, set as an ACA registry credential via `az containerapp registry set --server ghcr.io --username <gh-user> --password <pat>`. ACA requires explicit registry credentials for any non-ACR registry, even for public images. ([Microsoft community thread on GHCR + ACA auth](https://techcommunity.microsoft.com/discussions/azure/trying-to-deploy-container-app-from-github-actions---authentication-failure/4021585)) **[VERIFIED]**
- **Runner-up (later):** ACR with managed-identity pull. Removes the PAT entirely and eliminates GHCR as a deploy-time dependency. Migration cost is low; recommend the swap post-demo. **[VERIFIED]** [SUSPECTED for timing]
- **Why not now:** ACR is ~$5/mo Basic + extra Bicep, and the global rule names GHCR.
- **CI → Azure auth:** GitHub OIDC federated credential trusting `repo:johnmathews/horizons:ref:refs/heads/main`. No client secrets in GitHub Actions. **[VERIFIED]**

### 14. Revision-based rollback flow

ACA keeps revisions and supports traffic splitting. ([Microsoft Learn: Traffic splitting](https://learn.microsoft.com/en-us/azure/container-apps/traffic-splitting), [Manage revisions](https://learn.microsoft.com/en-us/azure/container-apps/revisions)) **[VERIFIED]**

1. App in `activeRevisionsMode: Multiple` — otherwise ACA deactivates the previous revision and rollback needs a redeploy.
2. Deploy creates revision `--sha-NEW`; traffic still on `--sha-OLD`.
3. CI smoke-tests `--sha-NEW`'s revision-specific FQDN.
4. **On pass:** `az containerapp ingress traffic set --revision-weight ca-horizons-api--sha-NEW=100 ca-horizons-api--sha-OLD=0`. Atomic flip.
5. **On fail:** CI does not touch traffic. Previous revision keeps serving. Failure posted to Slack.
6. **Manual rollback:** same `traffic set` command, `--sha-OLD=100`, from operator laptop with `az login`. Sub-second cutover.

Document the exact commands in `docs/operations/rollback.md`. Old-revision retention: 10 active for 7 days then deactivate (scripted in deploy workflow).

### 15. Migrations — separate ACA Job, before traffic shift

- **Choice:** ACA Job `cj-horizons-migrate`, manually triggered by the deploy workflow, runs `alembic upgrade head`. Triggered after image build, before `containerapp update`.
- **Runner-up A — API startup hook:** rejected. With `min ≥ 1` and multi-replica, replicas race the upgrade. Alembic's advisory lock prevents corruption but only one replica wins. More importantly, schema changes before the old revision stops serving — exactly the safety problem we're trying to bound.
- **Runner-up B — CI step from GitHub runner:** rejected. Requires opening Postgres firewall to GitHub IPs or running a self-hosted runner. The Job runs inside the ACA environment and gets the same identity/network story.
- **Why a Job:** single-replica by definition (`parallelism: 1, replicaCompletionCount: 1`), explicit success/failure, idempotent. Same image as the API with a different entrypoint.

### 16. Backward-incompatible migration policy

Two revisions briefly run in parallel during the flip, both against one Postgres. Policy:

1. **Every migration backward-compatible with N-1.** Adding a column fine; `NOT NULL` without default isn't.
2. **Drops are three-step:** release N stops writing, N+1 stops reading, N+2 drops.
3. **Renames are multi-step:** create new column → dual-write → backfill → swap reads → stop writing old → drop. Never `RENAME` in a single migration shipped with code using the new name.
4. **RLS policy changes** (doc 4 §"Public API / How"): same expand-contract rule. Stricter policies safe immediately; looser policies wait for all consumers.
5. **Long migrations** (anything heavy on `change_events`): run as a separate maintenance Job, not in the deploy path.

Document in `docs/operations/migrations.md`.

---

## E. SPA deployment

### 17. SPA hosting — Storage `$web` + Azure Front Door Standard

- **Choice:** Storage Account `sthorizonsspa` with the static website feature enabled. SPA built in CI, uploaded to `$web`. Azure Front Door Standard for CDN, TLS, custom domain. ([Microsoft Learn: Static website hosting in Azure Storage](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website)) **[VERIFIED]**
- **Runner-up:** Azure Static Web Apps. Rejected: (a) we already have an auth model in the API, (b) SWA's built-in auth doesn't match self-rolled JWT cleanly, (c) the demo needs fewer moving parts.
- **Why Front Door over CDN:** Azure CDN Standard from Microsoft is being retired (managed certs there expired April 2026); Front Door Standard is the active product. **[VERIFIED]**
- **SPA routing:** Front Door rewrite rule sends 404s to `/index.html`. **[VERIFIED]**
- **Cache invalidation:** content-hashed filenames (Vite default); `index.html` gets `Cache-Control: no-cache`. Deploy also runs `az afd endpoint purge --content-paths "/index.html"`.

### 18. SPA config — runtime, not build-time

- **Choice:** Runtime `/config.json` fetched on boot. Holds API base URL, feature flags, the alignment-confidence default (doc 4 §"Webapp"), any demo-tunable defaults.
- **Why:** Build-time env vars bake the API URL into the bundle, forcing one bundle per env and a rebuild for any tweak. Doc 4 and CLAUDE.md both name runtime tunables as a hard requirement.
- **Runner-up:** Build-time. Rejected for the redeploy-friction reason.
- **Auth:** token endpoint URL in `/config.json`; JWT public key fetched from `${API_BASE}/.well-known/jwks.json` (sets up the Entra External ID swap later).

---

## F. Observability

### 19. HTTP-shape metrics — ACA built-in + OTEL to App Insights

- **Choice:** ACA's built-in Log Analytics + Container Apps metrics + the managed OTEL agent forwarding to Application Insights. ([Microsoft Learn: OpenTelemetry in ACA](https://learn.microsoft.com/en-us/azure/container-apps/opentelemetry-agents)) **[VERIFIED]**
- **Why:** ACA gives Requests, RequestsFailed, Replicas, CPU, memory out of the box — covers rate and replica count but not p95 latency or per-endpoint shape, which the admin observability requirement names. Instrument FastAPI with `opentelemetry-instrumentation-fastapi`; the managed agent forwards traces/logs at zero compute cost. Latency histograms come from the trace spans.
- **Runner-up:** Prometheus sidecar. Rejected: doubles per-replica memory for marginal gain at this scale.

### 20. Admin metrics endpoint — query Log Analytics server-side

- **Choice:** Admin endpoints (`/v1/admin/health/...` per doc 4) call the Log Analytics Query API server-side via the UAMI (granted `Log Analytics Reader` on the workspace). Returns rate/p95/error windowed at 5/15/60 min.
- **Why:** Single source of truth. Self-emit `/metrics` + scraper + store is a second observability stack to maintain.
- **Runner-up:** Prometheus `/metrics` scraped by Azure Monitor managed Prometheus. Lower latency to first byte; more pieces. Defer until measured.
- **Cache:** 60s response cache to stop dashboard refresh storms hitting Log Analytics quotas.

### 21. Alerting — three rules, demo-scope

Action Group routes to one email + Slack webhook:

1. API error rate > 5% over 5 min → P2.
2. API p95 latency > 3 s over 10 min (the doc-3 SLO line) → P2.
3. Ingestion job failure (any execution failed) → P3.

Post-demo: add PagerDuty + a fourth rule ("no successful ingestion in 24h").

---

## G. Cost & scaling sanity

### 22. Rough monthly cost (USD, demo-scale and early-customer scale)

| Component | Demo scale | Early customer (1k req/min, 100 docs/day) |
|---|---|---|
| ACA (API, 1 replica avg 0.5 vCPU + 1 GiB always-on) | ~$25 | ~$120 (avg 3 replicas) |
| ACA Job (worker, 4 runs/hr × 1 min × 1 vCPU) | ~$2 | ~$15 |
| Postgres Flexible Server (B1ms demo / D2s_v3 prod) | ~$15 | ~$160 |
| Storage (Blob + static site, <10 GiB) | ~$1 | ~$5 |
| Front Door Standard | ~$35 (base) | ~$45 |
| Key Vault | ~$1 | ~$2 |
| Log Analytics + App Insights (5 GiB ingest demo) | ~$15 | ~$50 |
| **Total** | **~$95** | **~$400** |

[SUSPECTED] — these are envelope numbers. Re-baseline against the Azure Pricing Calculator before signing anything.

- **ACA scale-to-zero on the API — demo risk:** scale-to-zero cold start is a few seconds to ~30s depending on image size ([Microsoft Learn: Reducing cold-start time](https://learn.microsoft.com/en-us/azure/container-apps/cold-start)). **For the demo: set min=1 on the API.** Cost delta is ~$15 over the demo window. Worth it to never have a first-impression cold start in front of bankers.

### 23. Cheap-vs-upgrade for demo window

- **Keep cheap:** Postgres B1ms (burstable), single replica. Worker as Job not App. Log Analytics with 7-day retention. No Front Door Premium.
- **Upgrade for demo:** API `min=1`. Storage geo-redundancy off (single region is fine for ephemeral demo). Postgres backup retention 7 days. Pre-warm caches: a `warmup` job runs an hour before demo that issues the headline queries against the API to populate Postgres buffer pool.
- **Specifically don't pay for:** Azure Front Door Premium (WAF custom rules), ACR (use GHCR for now), Postgres HA replica, Premium Key Vault, Application Insights Profiler.

---

## H. Top 5 risks specific to this shape

### 24. Risk register

1. **API cold-start hits demo first impression.** Mitigation: `min=1`, pre-warm script, premium ingress flag if region supports. *Likelihood demo-day: medium; impact: high.*
2. **Migration applied before traffic shift breaks the old revision mid-flip.** Mitigation: expand-contract policy (§16); CI lint that fails any migration containing `DROP COLUMN`/`ALTER COLUMN ... TYPE`/`DROP TABLE` without an `# expand-step-of: <prev-rev>` marker. *Medium / high.*
3. **GHCR pull flake during deploy.** GHCR has had outages; ACA pulling from it adds a third-party dependency. Mitigation: long-lived PAT with expiry watch; post-demo move to ACR + managed identity; retry image pull in CI. *Low-medium / medium.*
4. **Postgres connection exhaustion under multi-replica API + RLS session settings.** B1ms allows ~50 connections; a request running `SET app.user_id = ...` on a fresh connection without pooling burns through fast. Mitigation: PgBouncer (transaction-pooled) **or** app-side pool with `SET LOCAL` inside the txn (RLS-with-pooler is a known interaction). *Medium / high.*
5. **Blob egress charges if a 20 MB markdown gets hot.** Azure outbound is ~$0.08/GiB after 100 GiB. Mitigation: serve originals through Front Door; rate-limit the "download original" endpoint behind auth. *Low / low.*

Honourable mentions for `docs/operations/risks.md`:
- ACA region quota for environments (default is low; raise pre-demo).
- Log Analytics ingestion cap (5 GiB/day free, then $2.30/GiB).
- Front Door cache-purge propagation (~10 min worst case).
- Postgres Flex maintenance window — pin to 04:00 UTC on a non-demo day.

---

## Open questions to resolve before Phase 2 starts writing modules

1. Single-region (West Europe?) vs multi-region. Doc 4 says read replica is a lever, not redesign — so single-region is fine for now.
2. Custom domain for the demo: `demo.horizons.example` via Front Door — needs DNS access. Who owns the zone?
3. PAT vs ACR cutover date — recommend "demo+1 week".
4. Whether the migration job runs `alembic upgrade head` *unconditionally* or only when there's a new migration (cheap check via revision metadata).
5. The "admin-as-client support view" decision from doc 4 §"Open questions" affects how the observability admin endpoint identifies the requesting actor in App Insights traces.

---

*References (key citations only — full list in CI when this is regenerated):*
*- [Microsoft Learn: Update and deploy changes in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/revisions)*
*- [Microsoft Learn: Traffic splitting in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/traffic-splitting)*
*- [Microsoft Learn: Jobs in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/jobs)*
*- [Microsoft Learn: Manage secrets in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)*
*- [Microsoft Learn: Connect with Managed Identity (PostgreSQL)](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity)*
*- [Microsoft Learn: Reducing cold-start time on Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/cold-start)*
*- [Microsoft Learn: Collect and read OpenTelemetry data in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/opentelemetry-agents)*
*- [Microsoft Learn: Static website hosting in Azure Storage](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website)*
*- [Microsoft Learn: Publish revisions with GitHub Actions](https://learn.microsoft.com/en-us/azure/container-apps/github-actions)*
*- [Microsoft Learn: Comparing Terraform and Bicep](https://learn.microsoft.com/en-us/azure/developer/terraform/comparing-terraform-and-bicep)*
*- [Bicep vs Terraform Azure 2026 (technspire)](https://technspire.com/en/blog/bicep-vs-terraform-azure-2026-honest-update)*
*- [KEDA Postgres scaler](https://keda.sh/docs/2.13/scalers/postgresql/)*
*- [GHCR + ACA auth (Microsoft Community Hub thread)](https://techcommunity.microsoft.com/discussions/azure/trying-to-deploy-container-app-from-github-actions---authentication-failure/4021585)*
