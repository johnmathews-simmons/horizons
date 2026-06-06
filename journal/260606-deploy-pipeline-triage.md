# 2026-06-06 — Deploy pipeline triage

**Status:** all 8 bugs in the ledger are closed; the deploy pipeline lands a full stack against `horizons-nonprod`. Session retro + post-demo punch list live in `journal/260606-deploy-pipeline-end-to-end.md`. This doc is preserved as the bug-by-bug record but should not be edited further.

---

Working document for the "charge through and fix everything" effort. Created mid-session after the first end-to-end deploy attempts revealed multiple Bicep ↔ application contract mismatches that none of the unit/integration test suite catches. The repo is functionally complete (tests pass, Playwright e2e green locally), but no commit has ever produced a running Azure stack. This doc was the single source of truth for what's broken, what depends on what, and the order in which we fix it.

## Where we are right now

### Resource group `horizons-nonprod`

Bicep `main.bicep` has been run repeatedly through the day. Current state:

- **VNet** `horizons-dev-vnet` (`10.20.0.0/16`) with `snet-pgsql` (`10.20.0.0/24`) and `snet-aca` (`10.20.4.0/23`). Both delegated, no NSGs.
- **Private DNS zone** `privatelink.postgres.database.azure.com` linked to the VNet. A record `d1b6a3d3b188 → 10.20.0.4` matches the CNAME from the public FQDN. DNS resolution works.
- **Log Analytics** `horizons-dev-law` + **App Insights** `horizons-dev-appi` (workspace-based). LAW now bound to the ACA env (wired one-off this session — `az containerapp env update --logs-destination log-analytics …`).
- **Key Vault** `horizons-dev-kv-nyeovjum`. Only secret stored: `postgresAdminPassword`. **No JWT keys, no DB URL, no Lawstronaut creds.**
- **Storage** account exists; `$web` container exists. SPA never uploaded.
- **Postgres Flexible Server** `horizons-dev-pgsql`, PG 18, Burstable B1ms, VNet-integrated. State `Ready`. Database `horizons` created manually this session (was missing because no Bicep resource declared it).
- **ACA env** `horizons-dev-cae` with Consumption workload profile. VNet-integrated, external ingress.
- **Container Apps** `horizons-dev-api` (7 revisions, all `ActivationFailed`), `horizons-dev-worker` (7 revisions, all `ActivationFailed`).
- **Migration Job** `horizons-dev-migrate`. 3 executions, all `Failed` — containers stuck in `Waiting / Unknown on legion`, never produced any log.
- **Alerts** (5xx, p95, ingestion). Created, disabled. Queries now `union isfuzzy=true (datatable …), AppRequests | …` with `evaluationFrequency: PT5M`. KQL validates.
- **Front Door** profile + endpoint exist. SPA never uploaded, route untested.

### What changed this session (commits on top of pre-debug `35f7d29`)

| SHA | Summary |
|---|---|
| `50b2bd2` | Wrap alert KQL in `union isfuzzy=true` (first attempt at fresh-workspace alert deploy) |
| `6646373` | Add empty-datatable sentinel to fuzzy-union alert queries (second attempt) |
| `ea15851` | Bump API alert `evaluationFrequency` PT1M → PT5M |
| `764aa14` | Split Postgres into its own stack (`deploy-postgres.yml`); `main.bicep` reads server FQDN via `existing` lookup |

Plus two manual one-offs on the resource group (NOT in IaC):

1. `az postgres flexible-server db create --database-name horizons` — the `horizons` database. **Must be encoded into `infra/postgres.bicep` so fresh deploys are reproducible.**
2. `az containerapp env update --logs-destination log-analytics --logs-workspace-id <law-id>` — wire ACA console/system logs to LAW. **Must be encoded into `infra/modules/container-app-env.bicep`.**

## The bug ledger

Each row is a discrete, independently fixable bug. Fix order is bottom-up (database/secrets first, then containers, then end-to-end). Mark `[x]` when committed and verified.

### B1 — API Dockerfile CMD references nonexistent module-level `app`

- **File:** `packages/horizons-api/Dockerfile:89`
- **Current:** `CMD ["uvicorn", "horizons_api.app:app", "--host", "0.0.0.0", "--port", "8000"]`
- **Reality:** `horizons_api.app` only exposes a `create_app()` factory; there is no module-level `app` symbol. Uvicorn's "Attribute 'app' not found in module 'horizons_api.app'" error in the ACA system log confirms this.
- **Fix:** Use uvicorn's factory mode: `CMD ["uvicorn", "horizons_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]`.
- **Verification:** New image, deployed revision reaches `Running` not `ActivationFailed` (assuming B5 also fixed for env vars).
- **Status:** [x] (landed; see end-to-end retro)

### B2 — Migration tooling absent from API runtime image

- **File:** `packages/horizons-api/Dockerfile` runtime stage (lines 62–89)
- **Symptom:** `migration-job.bicep` reuses the API image and runs `uv run alembic upgrade head`. But the runtime stage copies only `/opt/venv`; it doesn't copy `alembic.ini` (workspace root) or `packages/horizons-core/migrations/`. And `uv` is in `/usr/local/bin/` of the **builder** stage only; not in the runtime image.
- **Fix:** Copy alembic.ini + migrations to runtime stage, drop `uv` dependency by running `alembic` directly (it's installed into `/opt/venv/bin/` as a regular console script via the horizons-core dep tree).
  ```dockerfile
  # In the runtime stage, before USER horizons:
  COPY alembic.ini ./alembic.ini
  COPY packages/horizons-core/migrations ./packages/horizons-core/migrations
  ```
- **Verification:** Inside the new image, `alembic --version` succeeds and `ls /app/alembic.ini` shows the file. (Also B4 needs to be fixed before migrations actually run.)
- **Status:** [x] (landed; see end-to-end retro)

### B3 — Migration job command uses missing `uv`

- **File:** `infra/modules/migration-job.bicep:153-159`
- **Current:** `command: ['uv', 'run', 'alembic', 'upgrade', 'head']`
- **Fix:** Once B2 is fixed, drop `uv run` and call `alembic` directly. But we also need to construct `HORIZONS_DB_URL` from the individual env vars (see B4), so the command becomes a sh wrapper:
  ```bicep
  command: [
    'sh'
    '-c'
    'export HORIZONS_DB_URL="postgresql+psycopg://${env.HORIZONS_DB_USER}:${env.HORIZONS_DB_PASSWORD}@${env.HORIZONS_DB_HOST}:5432/${env.HORIZONS_DB_NAME}"; exec alembic upgrade head'
  ]
  ```
  *Note: Bicep interpolates `${…}` at compile time. Need to escape: the env-var refs above must be passed through as `\${HORIZONS_DB_USER}` etc. in the Bicep source so that the literal `${…}` lands in the JSON and shell expansion happens at runtime.* See the actual Bicep syntax in B4 below.
- **Verification:** New job execution logs Alembic INFO lines and exits 0.
- **Status:** [x] (landed; see end-to-end retro)

### B4 — Migration job env vars don't match what `migrations/env.py` reads

- **File:** `infra/modules/migration-job.bicep` env block (lines 164–195)
- **Current:** Sets `HORIZONS_DB_HOST`, `HORIZONS_DB_NAME`, `HORIZONS_DB_USER`, `HORIZONS_DB_PASSWORD` (secret).
- **Reality:** `packages/horizons-core/migrations/env.py:22` reads `HORIZONS_DB_URL`. There is no code path that assembles a URL from the individual vars.
- **Fix options:**
  - **(a, preferred)** Add a shell wrapper in `command:` that builds `HORIZONS_DB_URL` from the individual vars and execs alembic (paired with B3). Pros: zero application change. Cons: shell quoting needs care.
  - **(b)** Extend `migrations/env.py` to construct the URL from individual vars when `HORIZONS_DB_URL` is absent. Cleaner long-term, but touches application code.
- **Recommendation:** (a) for the demo, (b) post-demo.
- **Verification:** Same as B3.
- **Status:** [x] (landed; see end-to-end retro)

### B5 — API container has no DB or JWT env vars

- **File:** `infra/modules/container-app-api.bicep:96-105`
- **Current env block:**
  ```bicep
  env: [
    { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
    { name: 'HORIZONS_ENV', value: environmentName }
  ]
  ```
- **What the app needs at startup** (from `horizons_api/config.py` and `horizons_core/db/session.py`):
  - `HORIZONS_DB_URL` (required)
  - `HORIZONS_JWT_PRIVATE_KEY_PEM` (required, PEM-encoded RS256 private key)
  - `HORIZONS_JWT_PUBLIC_KEY_PEM` (required, matching public key)
  - `HORIZONS_JWT_ISSUER` (required)
  - `HORIZONS_JWT_AUDIENCE` (required)
  - `HORIZONS_CORS_ORIGINS` (optional but the webapp won't work without it)
- **Fix sketch:**
  1. Generate an RS256 keypair (one-off): `openssl genpkey -algorithm RSA -out priv.pem -pkeyopt rsa_keygen_bits:2048 && openssl rsa -pubout -in priv.pem -out pub.pem`.
  2. Store both PEMs as Key Vault secrets `horizonsJwtPrivateKeyPem`, `horizonsJwtPublicKeyPem`.
  3. Add ACA-level secrets backed by those KV refs (`identity` + `keyVaultUrl` syntax in `secrets:` block).
  4. Add env vars referencing those secrets via `secretRef`.
  5. Add `HORIZONS_DB_URL` as a literal env var (or KV-backed secret) constructed from the postgres FQDN + admin password.
  6. Add `HORIZONS_JWT_ISSUER`/`HORIZONS_JWT_AUDIENCE` as plain env vars.
  7. Add `HORIZONS_CORS_ORIGINS` — value should be the Front Door endpoint hostname (`https://horizons-dev-<hash>.azurefd.net` or whatever it resolves to).
- **Prereq:** UAMI needs `Key Vault Secrets User` role on the vault; ACA needs the UAMI mounted (currently ACA uses `SystemAssigned` — switch to or add `UserAssigned`).
- **Verification:** New revision reaches `Running`. `/healthz` returns 200 from inside the ACA env.
- **Status:** [x] (landed; see end-to-end retro)

### B6 — Worker container has no DB or domain env vars

- **File:** `infra/modules/container-app-worker.bicep:94-107`
- **Current env block:** Only `APPLICATIONINSIGHTS_CONNECTION_STRING`, `HORIZONS_ENV`, `HORIZONS_WORKER_HEALTH_PORT`.
- **What the worker needs** (from `horizons_ingestion/config.py:from_env` and `__main__.py`):
  - `HORIZONS_DB_URL` (required — KeyError on startup if absent)
  - `HORIZONS_INGESTION_BLOB_ACCOUNT_URL` (e.g. `https://horizonsdevstor.blob.core.windows.net`)
  - `HORIZONS_INGESTION_BLOB_CONTAINER` (defaults `originals`; ours is also `originals`)
  - Several tuning knobs with safe defaults — skip unless tuning needed.
  - **Lawstronaut credentials.** Confirm whether the worker calls Lawstronaut directly at runtime in the demo, or whether the curated seed already populated the corpus and the worker just maintains it. **Open question — see Q3.**
- **Fix:** Same pattern as B5 — KV-backed secrets for the password, plain env vars for the rest.
- **Verification:** New revision reaches `Running` and `/healthz` returns 200 on port 8080.
- **Status:** [x] (landed; see end-to-end retro)

### B7 — `horizons` database not in IaC

- **File:** `infra/postgres.bicep`
- **Current:** Server is provisioned but no databases.
- **Fix:** Add a `databases` child resource:
  ```bicep
  resource horizonsDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
    parent: pgsql  // declared in postgres-flex.bicep, expose via output if needed
    name: 'horizons'
    properties: {
      charset: 'UTF8'
      collation: 'en_US.utf8'
    }
  }
  ```
  Add the resource inside `infra/modules/postgres-flex.bicep` next to the server, OR in `infra/postgres.bicep` if we keep it stack-level.
- **Verification:** Tearing down the database and re-running `deploy-postgres.yml` recreates it.
- **Status:** [x] (landed; see end-to-end retro)

### B8 — ACA env log destination not in IaC

- **File:** `infra/modules/container-app-env.bicep`
- **Current:** No `appLogsConfiguration` block (was removed in commit `5eb35d6` because the previous shape was broken).
- **Fix:** Add the correct shape:
  ```bicep
  appLogsConfiguration: {
    destination: 'log-analytics'
    logAnalyticsConfiguration: {
      customerId: logAnalyticsCustomerId  // workspace.properties.customerId — new param
      sharedKey: logAnalyticsSharedKey    // listKeys(workspace, …).primarySharedKey — new @secure param
    }
  }
  ```
- **Verification:** Fresh `az containerapp env show` returns `appLogsConfiguration.destination = log-analytics` without manual intervention.
- **Status:** [x] (landed; see end-to-end retro)

## Cross-cutting concerns

### Secret architecture

Today: `postgresAdminPassword` in KV; everything else in `secrets.POSTGRES_ADMIN_PASSWORD` on the GitHub Environment. To support B5/B6 properly we need:

1. KV stores: `postgresAdminPassword`, `horizonsJwtPrivateKeyPem`, `horizonsJwtPublicKeyPem`, and a derived `horizonsDbUrl` (the full URL with admin password baked in).
2. ACA Container Apps each reference KV secrets via `secrets:` block with `keyVaultUrl` + `identity: <UAMI ID>`. This requires:
   - The UAMI must have `Key Vault Secrets User` role on the KV.
   - Each Container App must be configured to use the UAMI (not just SystemAssigned).
3. The deploy.yml workflow's `POSTGRES_ADMIN_PASSWORD` GH Environment secret is the source-of-truth for the bootstrap pass (used to populate KV the first time, then KV is the runtime source).

**Decision:** for the demo we can short-circuit and skip KV indirection — write the secrets directly into the ACA Container Apps' `secrets:` block, sourced from Bicep `@secure()` params passed in by the workflow. Less elegant, much faster. Recipe below.

### UAMI identity

Already exists: `horizons-github-oidc`. Currently has `Contributor` on the RG and `Storage Blob Data Contributor` on storage. We need:
- `Key Vault Secrets User` (if going the KV route).
- Possibly DB-side `pgaadauth_create_principal` for the AAD-passwordless target state. Skip for demo; we'll use the password fallback.

### Image rebuild

After B1/B2 fixes land, the GHCR images need a fresh build. `build-and-push.yml` triggers on push to main. Each Bicep image fix → push → wait for `build-and-push` → `deploy.yml` auto-fires. Roughly 8–12 min per cycle. Plan accordingly.

## Decisions (2026-06-06, locked)

- **Q1 — Secret architecture:** Short-circuit Key Vault. Bicep `@secure()` params → ACA `secrets:` block direct. Post-demo refactor to KV-backed `keyVaultUrl` references is a known follow-up.
- **Q2 — JWT iss/aud:** `HORIZONS_JWT_ISSUER=horizons-api-dev`, `HORIZONS_JWT_AUDIENCE=horizons-webapp-dev`.
- **Q3 — Lawstronaut at demo time:** Static. Curated set + synthetic v2 are pre-seeded; worker idles on its loop. No Lawstronaut credentials needed in the worker container.
- **Q5 — Demo accounts:** New ACA Job mirroring the migration job pattern, fired from `deploy.yml` after migrations succeed.

## Still to decide (lower-leverage)

- **Q4 (CORS origin):** Use `frontDoor.outputs.endpointHostName` as the runtime source. The SPA build needs the API FQDN at build time via `VITE_API_BASE_URL` — checked at the deploy.yml SPA-build step when we get there.
- **Q6 (SPA build env):** Likely the SPA has never been built with a non-localhost API URL. Audit at deploy.yml SPA-build time.

## Fix order

Sequenced to minimise rework. Each step is a discrete commit.

1. **B7 + B8** (IaC parity). Cheap to add; once landed, fresh resource groups can rebuild. Bonus: B8 unlocks readable logs for all future fixes — critical.
2. **B1 + B2** (API Dockerfile). Same file, single commit. Rebuilds the image.
3. **B3 + B4** (migration job). Once B2's image has alembic + migrations, the job can succeed.
4. **B5** (API env). Heaviest — touches container-app-api.bicep + needs JWT keys generated.
5. **B6** (Worker env). Mirrors B5 structurally.
6. Run a full deploy. Iterate on whatever surfaces next.
7. SPA build + upload validation.
8. Demo accounts run.

Between (3) and (4), pause to confirm Q1–Q4 with John.

## Validation checklist (end-to-end demo readiness)

- [ ] `az containerapp job execution show … horizons-dev-migrate-<id>` returns `Succeeded`.
- [ ] `az containerapp revision list --name horizons-dev-api` shows the latest revision in `Running` with `healthState=Healthy`.
- [ ] Same for `horizons-dev-worker`.
- [ ] `curl https://<api-fqdn>/healthz` returns 200.
- [ ] `curl https://<front-door-host>/` returns the SPA's `index.html`.
- [ ] Demo user (`demo-uk@example.test`) can log in via the SPA against the deployed API.
- [ ] Clause-diff page renders for at least one curated set v1 → v2 pair.
- [ ] Worker `/healthz` reachable from inside the ACA env (probe state should reflect this).

## Notes for future me

- The repeated `ServerIsBusy` on Postgres was *not* a real Postgres bug. It was the RP's response to re-asserting the server's `administratorLoginPassword` on every routine deploy, taking a control-plane lock for ~5 min that the next deploy collided with. The fix (split Postgres into its own stack) is the right long-term shape regardless.
- The alert KQL `union isfuzzy=true (datatable …), AppRequests` pattern + `PT5M` evaluation frequency is the production-correct shape for fresh-workspace deploys. Don't revert post-demo even if logs are flowing.
- The two manual one-offs (`flexible-server db create`, `containerapp env update --logs-destination`) are the kind of thing that should never be one-off if the IaC claims to be reproducible. Both are scheduled as B7/B8.
- The Dockerfile comment at line 87 ("Until WU4.1 lands, the symbol doesn't exist and the container will exit at startup — that's a feature, not a bug") is now actively misleading. WU4.1 landed and the CMD still doesn't match. Remove that comment when fixing B1.

## Post-fix note: SPA-deploy purge speedup (a2eb26e, 567b546)

The `deploy-spa` job's "Purge Front Door cache" step was the long pole on green deploys — `az afd endpoint purge` blocks until the purge has propagated to every edge node, which Azure can take several minutes to confirm. The step is *necessary* (`/`, `/index.html`, `/config.json` aren't content-hashed by Vite, so without a purge Front Door serves the prior build's HTML pointing at the prior build's `config.json`), but the *wait* isn't: nothing in `deploy.yml` consumes the "purge finished" signal — this is the last step of `deploy-spa` and no downstream job gates on it. Added `--no-wait`; the API call still issues the purge, the CLI returns as soon as Azure accepts it, and propagation finishes in the background. Tradeoff: for ~a minute after the workflow goes green, some edges may still serve the prior `index.html`/`config.json`. Fine for staging/demo; re-evaluate before any prod cutover.

## Aside: pre-commit hook silently uninstalled

During this fix the remote `Python CI / Pre-commit (all files)` job failed on a single trailing space in the new comment — the local `git commit` had not run hooks at all. Root cause: `git config core.hooksPath` was set on the local repo to `.git/hooks` (the default location), and pre-commit's `install` refuses to clobber any non-default `core.hooksPath` even if the value points where it would install anyway. Unsetting (`git config --unset core.hooksPath`) and rerunning `uv run pre-commit install` wired the hook up. CLAUDE.md's first-time-setup block now flags this so the next checkout doesn't repeat it.
