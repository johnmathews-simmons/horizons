# 2026-06-06 — Deploy pipeline goes end-to-end

*Last revised: 2026-06-06.*
*Path: journal/260606-deploy-pipeline-end-to-end.md.*

Session retrospective for the multi-hour debugging push that took the deploy pipeline from "every step in the chain breaks" to "API + worker + SPA all serving 200" against the `horizons-nonprod` resource group. The companion in-flight working doc — `journal/260606-deploy-pipeline-triage.md` — has the bug-by-bug ledger; this entry records what we learned and what's still pending.

## Final state

| Component | URL / state |
|---|---|
| API | `https://horizons-dev-api.prouddune-35523793.westeurope.azurecontainerapps.io/healthz` → 200 |
| SPA via Front Door | `https://horizons-dev-crffaqcedbc7b4gk.z03.azurefd.net/` → 200 |
| Migration job | last execution `Succeeded`, all 12 alembic revisions applied |
| Worker | revision `sha-507d7ad3a57e`, `RunningAtMaxScale`, `Healthy`, `/healthz` 200 |
| Postgres | flex server `horizons-dev-pgsql`, PG 18, DB `horizons` exists |

## How we got here

The session opened on a `Deploy` workflow failure — `'where' operator: Failed to resolve table or column expression named 'AppRequests'` on the alerts module. That turned out to be the surface of a much deeper issue: **the Bicep IaC had been written and committed but never validated against an actual end-to-end deploy.** Tests passed, e2e was green locally, the journal said WU8.4 was "the final work unit" — but the pipeline had never produced a running stack.

The 8-bug ledger in `260606-deploy-pipeline-triage.md` catalogues each contract mismatch between the application code and the infrastructure manifests. The summary:

1. **Alerts KQL** — workspace-based App Insights doesn't register `AppRequests` / `AppTraces` until the API has emitted a request. `skipQueryValidation: true` doesn't bypass the RP's pre-flight table-resolution. Fix: `union isfuzzy=true (datatable(<columns>)[]), AppRequests | …` + bump `evaluationFrequency` from `PT1M` to `PT5M` (the fuzzy-union wrapper disqualifies the query from the "known table" fast-path required for 1-minute frequency).
2. **Postgres `ServerIsBusy`** — every routine deploy re-asserted the flex server and its `@secure()` admin password, taking a control-plane lock for ~5 min that the next deploy collided with. Fix: split Postgres into its own stack (`infra/postgres.bicep`, `.github/workflows/deploy-postgres.yml`); main.bicep reads the FQDN via an `existing` lookup.
3. **API Dockerfile** — `CMD ["uvicorn", "horizons_api.app:app", …]` referenced a module-level `app` symbol that didn't exist; `horizons_api.app` exposes a `create_app()` factory. Fix: `uvicorn --factory horizons_api.app:create_app`.
4. **Migration job image** — the API runtime image stripped dev deps (`uv sync --no-dev`); `alembic` + `asyncpg` + `psycopg` were all dev-only. Fix: promote to `horizons-core` runtime deps.
5. **Migration job command** — used `uv run alembic`, but `uv` wasn't in the runtime image (builder-stage only). Fix: drop `uv`, call `alembic` directly.
6. **Migration job DB URL** — env vars were `HORIZONS_DB_HOST/NAME/USER/PASSWORD`; `migrations/env.py` reads `HORIZONS_DB_URL`. Fix: wrap alembic in `sh -c "export HORIZONS_DB_URL=…; exec alembic upgrade head"`.
7. **API container env** — no DB URL, no JWT keys, no CORS origins. The runtime would have raised `RuntimeError` on first request. Fix: `secrets:` block sourced from `@secure()` Bicep params, JWT keypair generated locally and pushed as `secrets.HORIZONS_JWT_{PRIVATE,PUBLIC}_KEY_PEM` on the `staging` GitHub Environment.
8. **Worker container env** — same shape, plus `HORIZONS_INGESTION_BLOB_ACCOUNT_URL` and (the surprise) `LAWSTRONAUT_EMAIL` / `LAWSTRONAUT_PASSWORD` — the worker reads them at startup even in static-dataset mode (Q3) where they're never used. Dummy values let the worker boot.
9. **Migration role grants** — PG 18 + Azure Flex don't expose a superuser to the migration user. `ALTER … OWNER TO schema_owner` failed three times in a row: (a) migration user wasn't a member of `schema_owner`, (b) `schema_owner` had no CREATE on `public`, (c) `admin_bypass` had no CREATE on `app_private`. Fix: self-grant the migration user into each owner role and grant the owner roles `CREATE` on each schema they receive objects in.
10. **`horizons` database missing** — Bicep created the flex server but no databases. Fix: child `Microsoft.DBforPostgreSQL/flexibleServers/databases` resource.
11. **ACA env log destination** — `appLogsConfiguration` was a manual `az containerapp env update` step. Without it, the worker's `KeyError` traceback was invisible. Fix: wire `customerId` + `listKeys().primarySharedKey` into the env Bicep.
12. **SPA upload role + static-website flag** — `Storage Blob Data Contributor` on the GitHub OIDC UAMI + worker's SystemAssigned identity, plus `az storage blob service-properties update --static-website`. Both are manual one-offs (the role assignment is blocked by the UAMI lacking `User Access Administrator`; the static-website flag has no Bicep representation today).
13. **SPA runtime config shipped the dev default** — `packages/horizons-webapp/public/config.json` declares `apiBaseUrl: "http://localhost:8000"` for local `npm run dev`. Vite copies `public/*` into `dist/*` verbatim, and the deploy pipeline uploaded `dist/` unchanged. Live SPA on `https://horizons-dev-<...>azurefd.net` therefore tried to hit `http://localhost:8000/v1/auth/login` on click of *Sign in* — browser-blocked as mixed content, surfaced in the console as `CORS request did not succeed. Status code: (null)` (no response object reaches the page). Hotfix: download `$web/config.json` via storage key, `jq --arg url "https://<api_fqdn>" '.apiBaseUrl = $url'`, re-upload, purge `/config.json` on Front Door. Persistent fix: `prepare-infra` now exposes `apiFqdn` as a job output alongside `storageAccountName`, and `deploy-spa` has a new step between `npm run build` and `az storage blob upload-batch` that rewrites `dist/config.json` with `jq`. Same `tuningThresholds` + `featureFlags` are preserved from the bundled file.

19 commits, mostly small, plus the bug-#13 follow-up. The triage doc is the bug-by-bug detail.

## Five learnings worth carrying forward

### 1. "Tests pass" ≠ "deployable"

The repo had ~~600~~ tests including testcontainers integration coverage and a Playwright e2e suite. Every test passed. The first Azure deploy still revealed every bug listed above. The application's unit/integration tests exercise the application; the e2e exercises a locally-booted stack. Nothing exercises **the IaC → image → runtime contract**. That gap is where every bug in this session lived.

Concrete consequence: future "WU8.4-style" pre-demo wraps must include a real deploy to a clean RG as the gate. The cost of finding all of this in a real deploy window with a demo two days out is much higher than the cost of finding it during normal feature development.

### 2. Migrations vs Azure Flex assumptions

The migrations were written and tested against testcontainers PG, where the connecting user is a superuser. Azure Flex's admin login has `CREATEROLE` but is not a superuser. Anything the migration does that **requires** superuser (ALTER OWNER without explicit membership, ALTER OWNER without explicit CREATE on the schema) silently works in tests and silently fails in prod.

Specifically: every `op.execute("ALTER … OWNER TO <role>;")` is fragile against the non-superuser case. The fix isn't "remove the ALTER OWNER calls" — the role split is load-bearing for the security model. It's "the migration must self-bootstrap permission to do its own OWNER transfers." We landed that in 0001/0002/0009 this session.

The test that would have caught this: a testcontainers test that runs the migration as a non-superuser deliberately created with only `CREATEROLE`. Worth adding post-demo.

### 3. The "post-deploy one-off" anti-pattern compounds

CLAUDE.md and `docs/runbooks/deploy.md` both had a section listing several "first deploy follow-up" manual `az` commands. Each individually seemed fine; collectively they meant the IaC's claim to be "what you deploy is what you get" was false. The first deploy failed twice in ways that turned out to be missing one-offs (storage role, static-website flag). The ACA env log destination was the third — and arguably the worst, because without logs we were operating blind on every subsequent diagnosis.

The principle: every "one-off control-plane action" is a bug in the IaC unless documented as deliberately out-of-scope. Three of this session's commits encoded previously-manual one-offs into Bicep (B7, B8, the `horizons` database creation). Two are still one-offs because the UAMI doesn't have the permissions to make the role assignments itself.

### 4. ACA Container Apps revisions accumulate when broken

`activeRevisionsMode: 'Multiple'` (the API's default for blue/green) keeps every revision running and serving 0%-traffic replicas. When a stream of broken revisions deploys, each one keeps one crash-looping replica running indefinitely. The worker (which also uses `Multiple` — copied from the API) had 17 broken revisions all logging `KeyError` repeatedly into the same Log Analytics workspace, drowning out the latest revision's signal.

The worker's `Multiple` is unjustified — the worker has one always-on replica per ADR-0001, no traffic shift dance. It should be `Single`. Adding to the punch list.

### 5. Tooling: `az containerapp job logs show` is the right entry point

Once Log Analytics was wired up, `az containerapp job logs show --container migrate --execution <id>` was the fastest path to the actual stack trace. `az containerapp job execution show` returns nothing useful when the replica is in `Waiting` state (which was the case for the first ~4 migration failures); the LA query path showed nothing until we wired the env's destination. The CLI command worked even without LA wired because it streams from the container API directly. Worth knowing earlier next time.

## Post-demo punch list

In rough order of leverage; items here are explicit follow-ups, not nice-to-haves.

### IaC drift / parity

- [ ] Elevate `horizons-github-oidc` UAMI to `User Access Administrator` on `horizons-nonprod`, then move both `Storage Blob Data Contributor` assignments back into Bicep (storage module). Currently manual one-offs.
- [ ] Find a way to encode `az storage blob service-properties update --static-website --index-document index.html --404-document index.html`. Options: post-deploy script step in `deploy.yml`; an ACA Job that runs once at deploy time; a `deploymentScripts` Bicep resource.
- [ ] Set `activeRevisionsMode: 'Single'` on `container-app-worker.bicep`. The worker has one always-on replica per ADR-0001; `Multiple` is unjustified copy-paste from the API and causes broken revisions to accumulate.
- [ ] Encode the worker's SystemAssigned identity → Storage Blob Data Contributor assignment alongside the UAMI's (same blocker).

### Secret architecture (decision Q1 was "short-circuit, refactor post-demo")

- [ ] Move JWT private key + DB password into Key Vault, reference from ACA `secrets:` block via `keyVaultUrl` + `identity`. Today they're `@secure()` Bicep params, passed in via the deploy workflow each time. Requires the UAMI to have `Key Vault Secrets User` on the vault.
- [ ] Once moved, drop the `secrets.HORIZONS_JWT_{PRIVATE,PUBLIC}_KEY_PEM` GH Environment secrets. They're bootstrap-only.
- [ ] DB connection target state per `migration-job.bicep` header: UAMI registered as Postgres AAD principal, passwordless connection. The password fallback is the current posture.

### Migration robustness

- [ ] Add a testcontainers test that runs the migration as a non-superuser with only `CREATEROLE`, asserts each `op.execute("ALTER … OWNER TO …;")` succeeds. Would have caught all three permission-denied iterations in this session.
- [ ] Audit later migrations (0010–0012) for any further `ALTER OWNER` calls that bypass the bootstrap roles. The session's fixes cover 0002 and 0009; others may still be brittle if/when admin_bypass / api_app / ingestion_worker receive new objects.
- [ ] Factor the `users` + `admin_access_log` teardown sequence into a shared helper (or document the rule explicitly). `seed_e2e.py:_teardown` and `create_demo_accounts.py:_teardown` both need to set `session_replication_role = 'replica'` and DELETE the audit rows before deleting users (FK is `ON DELETE RESTRICT`, the audit table has an append-only trigger). Today the duplication caused the demo-account reseed to blow up on a Job execution because the rule was only encoded in one of them — fixed in commit `6e02027`, but the next teardown script will trip the same wire if nothing changes.

### Worker contract

- [ ] `LAWSTRONAUT_EMAIL` / `LAWSTRONAUT_PASSWORD` should be optional in the worker if no document is ever going to be claimed. Today the credential reader raises at startup. Adding a "static dataset mode" flag (read the env var only when the claim loop has work) is cleaner than dummy creds.
- [ ] Worker bicep currently doesn't expose `LAWSTRONAUT_*` as `@secure()` params. When this becomes a real Lawstronaut deployment, wire them as ACA secrets.

### Demo accounts (Task #7)

- [ ] New ACA Job mirroring `migration-job.bicep`'s pattern. Image = the API image. Command = `python -m horizons_api.scripts.create_demo_accounts` (or however the script is invoked from within the workspace). Triggered by `deploy.yml` after migrations succeed. Per decision Q5.
- [ ] Until that's in place, the SPA login screen has nothing to authenticate against — `demo-uk@example.test` etc. don't exist in the deployed DB.

### Auth cookie posture (accepted for demo)

- [ ] **Refresh cookie SameSite=None is a deliberate, demo-only choice.** As of commit `2632ee8` the refresh cookie is set with `SameSite=None` (was `Lax`) because the deployed SPA host (Front Door / Storage `$web`) and the API host (Container Apps default domain) are different *sites*; under `Lax` the browser withheld the cookie on the cross-site XHR to `/v1/auth/logout` (and to the cold-bootstrap `/v1/auth/refresh`). Full rationale, CSRF trade-off analysis, and remaining mitigations (`HttpOnly`, `Secure`, `Path=/v1/auth`, bearer-only data plane) in `journal/260606-fix-logout-samesite.md`. **John acknowledged this is acceptable for the 2026-06-08 demo window.** Post-demo: put the API behind Front Door at a sibling subdomain of the SPA (e.g. `app.<domain>` + `api.<domain>`) so the two are same-site under cookie rules, then revert to `SameSite=Lax`.

### Pipeline confidence

- [ ] First-fresh-deploy runbook. Tear down `horizons-nonprod`, recreate from zero, document every step. Should reduce to: provision UAMI + federated cred (one-off; can't be in repo); `gh workflow run deploy-postgres.yml`; `gh workflow run deploy.yml`. Anything that requires additional manual steps is a bug — see "IaC drift" above.
- [ ] CI step that runs `az bicep build` on every push to catch template errors before they reach the deploy step.
- [ ] Replace `git diff HEAD~1 HEAD -- infra/ .github/workflows/deploy.yml` in `deploy.yml`'s "Detect infra changes" step (L172) with a baseline that survives batched pushes. The current shape silently skips Bicep when infra changes sit deeper than the top two commits of a multi-commit push — burned us today by leaving the `activeRevisionsMode: Single` flip un-deployed for hours (see `journal/260606-stale-revision-and-reseed-teardown.md`). Options: diff against the SHA of the most recent successful ARM deployment, diff `${{ github.event.workflow_run.head_commit.id }}` against the run before, or remove the optimisation and always reconcile (Bicep is idempotent — cost is ~mins per deploy).
- [ ] `deploy-services` should also `az containerapp job update --image` for `horizons-dev-{migration,seed-demo-accounts,reseed-corpus}` Jobs. Today it only updates the API + worker container apps; the Jobs' images are baked into Bicep and only refresh when `prepare-infra` runs the full template — which means image-only pushes (most of them) leave the Jobs pinned to whatever was current at the last actual Bicep deploy. Burned us today: the demo-accounts teardown fix in commit `6e02027` shipped to GHCR within minutes, but the reseed Job kept failing for ~10 min because it was still on `sha-ea8f494ebe26` (the image baked at the last Bicep deploy several hours earlier). Fix is independent of the HEAD~1 baseline fix above: even with a correct baseline, image-only pushes don't trigger Bicep, so the Job image bumping must happen in `deploy-services` itself.

### Documentation

- [ ] `docs/runbooks/deploy.md` Prerequisites table needs:
  - `secrets.HORIZONS_JWT_PRIVATE_KEY_PEM` + `HORIZONS_JWT_PUBLIC_KEY_PEM` rows
  - The worker SystemAssigned identity's Storage Blob Data Contributor row
  - Update the "static-website" row to reference an explicit `az` command, with the exact `--index-document index.html --404-document index.html` flags
  - (Did this session — see commit history.)
- [ ] Removing the `infra/README.md` "post-deployment one-off steps" section once all items have been pulled into Bicep.

## Pointers

- Bug ledger (closed): `journal/260606-deploy-pipeline-triage.md`
- Final commit before this session: `35f7d29`
- Commits made this session: 19, see `git log 35f7d29..HEAD --oneline`
- Pending work: this entry's "Post-demo punch list" section above
