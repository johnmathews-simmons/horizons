# 2026-06-06 — WU8.4: pre-demo wrap

The final work unit. Two deliverables, both pure documentation: this
entry summarising the build, and a refresh of CLAUDE.md's `Commands`
section so the documented surface matches what actually ships.

Worktree `worktree-wu8.4-pre-demo-wrap`. No code, infra, or runbook
content touched.

The plan that drove this build is
`.engineering-team/runs/manual-20260604T151127Z/improvement-plan.md` —
58 originally-scoped WUs across eight tracks plus WU4.7 (added
mid-flight to unblock WU5.4). Final count: **59 / 59** shipped (or
**58 / 58** if WU4.7 is folded back into WU4.5 as originally intended).

## Build summary by track

### Track 0 — Repo scaffold + tooling (6/6)

- WU0.0 ✅ — Design-doc fixes (atomicity, RLS, confidence-threshold,
  effective_date). [`260604-initial-design-and-fixtures.md`].
- WU0.1 ✅ — `uv` workspace + three Python members + Vue 3 webapp.
- WU0.2 ✅ — ruff + pyright strict + pre-commit + eslint/prettier.
  [`260605-wu06-lint-check-wu10-pg-roles.md`].
- WU0.3 ✅ — pytest + testcontainers + Postgres 18 fixture.
- WU0.4 ✅ — `ci.yml` skeleton (lint + typecheck + test).
  [`260605-wu04-ci-skeleton-wu05-readme-license.md`].
- WU0.5 ✅ — `.gitignore`, README pointing at the design-doc chain,
  closed-source / no-LICENSE decision recorded in CLAUDE.md.

### Track 1 — Tenancy spine (10/10 — the highest-risk thing, built first)

- WU1.0 ✅ — Four Postgres roles (`schema_owner`, `api_app`,
  `ingestion_worker`, `admin_bypass`) + `core/db/roles.md`.
- WU1.1 ✅ — `users`, `subscriptions`, `subscription_scopes` with
  UUIDv7 PKs and append-only triggers. [`260605-wu11-tenancy-tables.md`].
- WU1.2 ✅ — `watchlists` + stub `change_events`.
  [`260605-wu12-corpus-tables.md`].
- WU1.3 ✅ — `app_private.current_scope()` SECURITY DEFINER function.
  [`260605-wu13-current-scope.md`].
- WU1.4 ✅ — RLS policies on private-state + corpus via
  `alembic_utils.PGPolicy`. [`260605-wu14-rls-spine.md`].
- WU1.5 ✅ — `async_session_factory` + `SET LOCAL` request bracket +
  `DISCARD ALL` on pool return. [`260605-wu15-session-bracket.md`].
- WU1.6 ✅ — Repository layer scaffold (mandatory keyword `user_id`,
  typed Pydantic returns).
  [`260605-wu16-and-wu17-repos-and-isolation-gate.md`].
- WU1.7 ✅ — Two-client integration test (the gate). Same journal.
- WU1.8 ✅ — Hypothesis property-isolation test, nightly.
  [`260605-wu18-hypothesis-property-isolation-nightly.md`]. Later
  marker-fixed in `260606-fix-property-isolation-strategy.md`.
- WU1.9 ✅ — Admin operator + impersonation sessions + audit-row write.
  [`260605-wu19-admin-operator-and-impersonation.md`].

### Track 2 — Parser + alignment pipeline (5/5)

- WU2.0 ✅ — Clause-tree parser, heading-anchored + inline-numbered.
  [`260605-wu20-clause-tree-parser.md`].
- WU2.1 ✅ — Generic recogniser + per-portal YAML overrides.
  [`260605-wu21-portal-configs.md`].
- WU2.2 ✅ — Similarity stack (shingle/MinHash/Jaccard/LSH) via
  `datasketch`, tunable. [`260605-wu22-similarity-stack.md`].
- WU2.3 ✅ — Three-pass alignment pipeline + `ChangeEvent` shape.
  [`260605-wu23-alignment-pipeline.md`].
- WU2.4 ✅ — Regression suite over the 31 fixtures + synthesised
  mutations. [`260605-wu24-alignment-regression-suite.md`].

### Track 3 — Ingestion worker (6/6)

- WU3.0 ✅ — Shape spike → ADR-0001: long-running asyncio loop, single
  replica. [`260605-wu30-worker-shape-spike.md`].
- WU3.1 ✅ — `documents`, `document_versions`, `document_poll_schedule`,
  `ingestion_incident`. [`260605-wu31-ingestion-tables-schema.md`].
- WU3.2 ✅ — `LawstronautClient` with pre-emptive refresh + asyncio
  lock + stamina retries + field-discrepancy tolerance.
  [`260605-wu32-lawstronaut-client.md`].
- WU3.3 ✅ — `FOR UPDATE SKIP LOCKED` claim loop + kill-switch + parked
  rows. [`260605-wu33-claim-loop.md`].
- WU3.4 ✅ — Per-document poll transaction (fetch → hash → align →
  insert atomically). [`260605-wu34-poll-transaction.md`].
- WU3.5 ✅ — `scripts/seed_curated_set.py`. [`260605-wu35-curated-set-seed.md`].

### Track 4 — Public API (7/7, plus WU4.7 plan-addition)

- WU4.0 ✅ — `TokenProvider` Protocol + `LocalJwtProvider` (RS256,
  argon2-cffi). [`260605-wu40-auth-seam.md`].
- WU4.1 ✅ — FastAPI shell + auth middleware + healthz.
  [`260605-wu41-fastapi-shell.md`]. + secfix
  [`260605-wu41-fix-token-kind-gate.md`] (token-kind enforcement at
  the auth boundary).
- WU4.2 ✅ — `/v1/auth/login`/`refresh`/`logout`, cookie + JSON
  shapes. [`260605-wu42-auth-endpoints.md`]. + secfix
  [`260605-wu42-securityfix-auth-hardening.md`] (cookie-source binding,
  argon2-on-miss, role re-read).
- WU4.3 ✅ — `/v1/me` + watchlists CRUD with scope-validation trigger.
  [`260605-wu43-me-and-watchlists.md`].
- WU4.4 ✅ — Three primitives (discovery / temporal / differential) at
  three scopes, paginated, with architectural test banning corpus
  table reads outside the repos. [`260605-wu44-three-primitives.md`].
- WU4.5 ✅ — Admin subscription endpoints with reduction soft-hide
  path. [`260605-wu45-admin-subscriptions.md`]. + secfix
  [`260605-wu45-secfix-scope-symmetry.md`] (reduction scope query
  realigned with `current_scope()` + server-side clock).
- WU4.6 ✅ — Auto-regen of `docs/api/endpoints.md` from FastAPI
  OpenAPI; pre-commit hook keeps it in sync.
  [`260605-wu46-openapi-endpoints.md`].
- **WU4.7 ✅ (plan-addition)** — `GET /v1/admin/clients` +
  `POST /v1/admin/impersonate`. Split out of WU4.5 because WU5.4
  needed both surfaces and the original plan had no place to put
  them. [`260606-wu47-admin-clients-and-impersonate.md`]. Second
  application of the named-adversary framing from the secfix
  retrospective.

### Track 5 — SPA webapp (7/7)

- WU5.0 ✅ — Vue 3 shell + Pinia + Vue Router + Tailwind v4 +
  shadcn-vue (Reka UI) + access-token-in-memory / refresh-in-cookie
  auth flow. [`260605-wu50-vue-shell-and-auth-store.md`]. + three
  open-redirect sanitiser iterations during review (commits
  `7ea4416` + `017ef3f` + `23a238f`).
- WU5.1 ✅ — Runtime `/config.json` fetched at boot.
  [`260605-wu51-runtime-config.md`].
- WU5.2 ✅ — Watchlist management view with scoped add-document
  modal, TanStack Vue Query. [`260605-wu52-watchlists-view.md`].
- WU5.3 ✅ — Change-browsing + clause-diff render with side-by-side
  ↔ unified toggle, alignment-confidence badge, default-off MOVED.
  [`260605-wu53-change-diff-view.md`].
- WU5.4 ✅ — Admin views + support view (operator-side adversary
  framing). [`260606-wu54-admin-views-support-view.md`]. Six
  adversary classes pinned by 34 new vitest cases.
- WU5.5 ✅ — `@tanstack/vue-virtual` for change list + Web Worker for
  diff on >1 MB docs. [`260605-wu55-large-doc-rendering-safety.md`].
- WU5.6 ✅ — Webapp CI build artifact + lint:check.
  [`260605-wu56-webapp-ci-build-artifact.md`].

### Track 6 — IaC + CI/CD (7/7)

- WU6.0 ✅ — Bicep skeletons (network, kv, postgres-flex, storage,
  ACA env + apps, App Insights, Front Door) + `main.bicep`.
  [`260605-wu60-bicep-skeletons.md`].
- WU6.1 ✅ — OIDC federation: single UAMI `horizons-github-oidc` +
  `staging`/`production` federated credentials + Contributor on
  `horizons-nonprod`. [`260605-wu61-oidc-federation.md`].
- WU6.2 ✅ — Dockerfiles for API + worker + `build-and-push.yml`
  → `ghcr.io/johnmathews/horizons-{api,worker}`.
  [`260605-wu62-dockerfiles-and-ghcr.md`].
- WU6.3 ✅ — `deploy.yml` with blue/green API revision flip + worker
  update + SPA upload + Front Door purge. The Bicep `traffic[]` block
  was dropped from the API ingress so deploy.yml owns traffic state.
  [`260605-wu63-deploy-pipeline.md`].
- WU6.4 ✅ — Migration ACA Job via Alembic against
  Postgres-Flex-managed identity. [`260605-wu64-migration-aca-job.md`].
- WU6.5 ✅ — Expand-contract policy + PR template checkbox.
  [`260605-wu65-migrations-runbook.md`].
- WU6.6 ✅ — `drift-check.yml` (nightly 03:00 UTC + `workflow_dispatch`)
  opening GH issues with label `infra-drift`.
  [`260605-wu66-drift-check-workflow.md`].

### Track 7 — Observability + admin views + audit (5/5)

- WU7.0 ✅ — `azure-monitor-opentelemetry` distro + FastAPI / SQLAlchemy
  / httpx auto-instrumentation. [`260605-wu70-otel-setup.md`].
- WU7.1 ✅ — structlog set up before FastAPI imports; JSON in prod,
  pretty in dev. [`260605-wu71-structlog-setup.md`].
- WU7.2 ✅ — `/v1/admin/health/{api,ingestion,db}` querying Log
  Analytics with 60 s cache. [`260605-wu72-admin-health.md`].
- WU7.3 ✅ — Three `scheduledQueryRules` alerts (API 5xx, p95, ingestion
  failures), all `enabled: false` by default + `actionGroups` with one
  email receiver. [`260605-wu73-alert-rules.md`].
- WU7.4 ✅ — `/v1/admin/audit` with filtering + immutable rows.
  [`260605-wu74-admin-audit.md`].

### Track 8 — Demo prep (5/5)

- WU8.0 ✅ — Curated set grown to 10 jurisdictions × 5 sectors + five
  hand-authored synthetic v2 fixtures + `stage_synthetic_v2()` in
  `horizons_ingestion.seed`. [`260605-wu80-demo-corpus-expansion.md`].
- WU8.1 ✅ — `create_demo_accounts.py` CLI + `docs/runbooks/demo-accounts.md`.
  [`260605-wu81-demo-accounts-cli.md`]. + two secfixes
  [`260605-wu81-secfix-demo-password-handling.md`,
  `260605-wu81-secfix2-no-downgrade-rotate.md`] (unconditional rotate
  → verify-against-cleartext no-downgrade guard).
- WU8.2 ✅ — Playwright e2e (`login-and-scope.spec.ts`) + `e2e.yml`
  workflow. [`260605-wu82-playwright-e2e-smoke.md`]. + five-bug hotfix
  [`260605-wu82-hotfix-e2e-cors.md`] — CORS, asyncpg driver, RFC-6761
  `.test` TLD, cold-bootstrap refresh, Authorization suppression on
  auth endpoints.
- WU8.3 ✅ — `docs/runbooks/demo.md`. [`260606-wu83-demo-runbook.md`].
  The WU5.4 placeholder it left was filled in by the runbooks update
  in commit `2751137`.
- WU8.4 ✅ — **this entry** + CLAUDE.md Commands refresh.

### Out-of-plan fixes that landed during the sprint

- `260606-fix-property-isolation-strategy.md` — Hypothesis strategy
  fix after migration 0009 added `watchlists.document_id NOT NULL`;
  also dropped the over-broad `@pytest.mark.integration` marker.
- `260605-fix-worker-staged-guard-and-env-validation.md` — DEMO-CRITICAL:
  `stage_synthetic_v2` now parks `document_poll_schedule.next_poll_at`
  to `2026-12-31` so the worker can't claim staged docs and silently
  degrade the demo's headline moment; plus a whitespace-stripping fix
  to the demo-accounts env-var resolver.

## Decisions taken vs deferred

| Decision | Taken | Deferred to post-demo |
| --- | --- | --- |
| API framework | FastAPI + uvicorn + SQLAlchemy 2.0 async + psycopg3 + Alembic + `alembic_utils.PGPolicy` | — |
| SPA framework | Vue 3 + Vite + Pinia + Vue Router + TanStack Vue Query + shadcn-vue (Reka UI) + Tailwind v4 + diff-match-patch | — |
| Auth | Self-rolled RS256 JWT via `LocalJwtProvider` + argon2-cffi + access-in-memory / refresh-in-cookie | `EntraIdProvider` swap-in via the `TokenProvider` Protocol |
| Database | Azure Postgres Flexible Server (PG 17), passwordless via managed identity in prod | Read replica when p95 justifies; PgBouncer |
| Blob storage | Azure Blob (`originals/<sha256>.md`) | — |
| Multi-tenant isolation | RLS + repo layer + lint-banned raw SQL + multi-user integration + Hypothesis property test | Per-poll metrics rollup vs raw rows |
| Subscription model | Normalised `subscriptions` + `subscription_scopes`; soft-hide reduction; service + trigger + RLS WITH CHECK | Per-document overrides; time-window-limited subscriptions; manual clause-alignment overrides |
| Ingestion worker shape | Long-running asyncio loop (ADR-0001); single replica | ACA Job on cron (the runner-up) |
| IaC + CI/CD | Bicep in `infra/` + GH Actions + OIDC federation + `ghcr.io/johnmathews/horizons-{api,worker}` + migration ACA Job + revision-based rollback | ACR migration; `cosign` + SBOM attestation; multi-revision soak / canary |
| SPA hosting | Storage `$web` + Azure Front Door Standard; runtime `/config.json` | SPA point-in-time rollback (versioned `$web` prefixes) |
| Observability | `azure-monitor-opentelemetry` distro → App Insights via ACA managed OTEL agent; structlog | Dynamic-threshold alerts; per-environment alert tuning; SMS / push receivers; Slack via Logic App |
| Admin model | Two paths: `BYPASSRLS` operator + impersonation token (15-min TTL); 2-row audit (operator + impersonation) per mint | `impersonator_id` propagated through Principal / OTEL / per-request audit |
| Repo shape | uv workspace `packages/horizons-{core,ingestion,api}` + standalone Vue webapp | — |
| Confidence-suppression threshold | 0.6 default, runtime-tunable | — |
| `effective_date` provenance | `publication_date + per_jurisdiction_default_lag` + per-doc overrides | In-document commencement parsing |
| Alert receivers | Single email (`mthwsjc@gmail.com` default) | Slack webhook via Logic App; SMS; push |
| Alert state at deploy | All three alerts `enabled: false` | Operator flips `alertsEnabled=true` on the next Bicep deploy after WU6.3's first deploy populates the workspace |
| Azure RBAC for the UAMI | `Contributor` on `horizons-nonprod` RG | Per-resource tailored roles (Container Apps Contributor, Storage Blob Data Contributor, KV Secrets User, Reader elsewhere) |
| Federated credential subjects | `staging` + `production` GitHub Environments on one UAMI | Per-environment UAMIs; `pull_request` cred for PR-time what-if; production cutover (no `horizons-prod` RG yet) |
| Production environment | GH Environment `production` exists with federated cred | Required-reviewers rule; branch constraint to `main`; prod RG; prod Postgres password secret; prod AAD principal; `main.parameters.prod.json` |
| Playwright e2e | Chromium only, single serial spec | Cross-browser (Firefox / WebKit); regression unit tests for hotfix Bugs 4 + 5 |
| Demo curated set | 10 jurisdictions / 5 sectors / 10 docs + 5 synthetic v2s | Grow `fixtures.json` to ~50 via `scripts/fetch_fixtures.py` |
| License | Closed-source / no LICENSE file (most-restrictive default copyright) | Formal license decision post-demo |

## Things to watch during the demo

Operator's heads-up list. The full recovery procedures live in
`docs/runbooks/demo.md`; this is the "what to keep one eye on" subset.

1. **ACA cold-start.** The first request after idle returns slowly
   (typically 30–60 s while the image pulls and the `/healthz` probe
   passes). Pre-warm by hitting `/healthz` before the audience joins.
2. **Front Door cache after a redeploy.** `deploy.yml` purges `/`,
   `/index.html`, and `/config.json` automatically, but a stale cache
   entry the audience hits before the purge fully propagates can show
   the previous bundle. Hard-refresh (Cmd-Shift-R) before the
   demonstration; a full reload from a private window after each
   redeploy is the cheapest sanity check.
3. **Alert rules disabled by default.** All three Azure Monitor alerts
   ship `enabled: false`. Leave them disabled for the showcase unless
   the operator has decided ahead of time that on-call signal during
   the public window is worth the noise (recall they sit in
   "Insufficient data" until App Insights has ≥ 5 min of real traffic).
   See `docs/runbooks/deploy.md` for Path A / Path B enable.
4. **`drift-check.yml` firing on push.** Cosmetic — drift detection is
   read-only and never modifies infra — but the workflow currently
   triggers on every push as well as on the nightly schedule
   (`260605-wu66-drift-check-workflow.md` documents the design as
   schedule + `workflow_dispatch`, so the push-trigger noise is a
   regression worth root-causing post-demo, not during).
5. **Browser cache after a SPA bundle redeploy.** Vite content-hashes
   the chunks, so `/index.html` is the only sticky surface. A user who
   loaded the SPA before the redeploy keeps running the old bundle
   until they reload. For the demo presenter, "private window per
   client switch" sidesteps the question entirely.
6. **15-minute impersonation token TTL.** If a demo segment runs long
   and the operator stays in support view past 15 minutes, the next
   API call returns 401, the SPA auto-exits support view, and the
   amber banner disappears mid-demo. The recovery (re-enter support
   view) is one click in the admin client detail page, but rehearse
   the segment so it finishes inside the TTL.
7. **`X-Client-Type: browser` is load-bearing.** The webapp sends this
   header to opt into the cookie-shaped auth response. The hotfix
   added it to the CORS allow-list; never remove the header from the
   webapp without also revisiting the cookie/refresh story.
8. **No teardown for staged synthetic v2.** `scripts/seed_curated_set.py`
   has no `--teardown`; `next_poll_at` for staged docs is parked at
   `2026-12-31`. Post-demo development that wants to resume polling
   needs to manually un-park or redo the seed.
9. **Demo-account passwords.** The CLI rotates on every run unless
   env-var-set + verify-match; the no-downgrade guard refuses to
   silently rotate a real production credential back to the public
   default. If the operator sees "refusing to rotate" during the
   pre-demo checklist, set the env vars or pass `--reset`.
10. **Worker idle during the demo.** WU8.0's operational rule is "do
    not run the worker against staged documents during the demo". The
    schedule-park guard makes this safe even if the worker is up, but
    the schedule rows for non-staged documents would still tick. Per
    `docs/runbooks/demo.md`, the worker stays paused for the duration.

## Post-demo TODOs

Concrete follow-ups captured across journal entries, roughly ordered
by load-bearing-ness. None of these block the demo; all are real.

1. **Regression unit tests for WU8.2-hotfix Bugs 4 + 5.** The
   cold-bootstrap refresh and the Authorization-suppression-on-
   auth-endpoints behaviour are currently only covered by the e2e
   test. Unit-level coverage in
   `packages/horizons-webapp/src/router/__tests__/` and
   `.../api/__tests__/` would catch regression on the next PR rather
   than at e2e time. (Flagged in `260605-wu82-hotfix-e2e-cors.md`.)
2. **Cross-reference note in `docs/runbooks/demo-accounts.md`** about
   the RFC-6761 `.test` TLD restriction so future demo-account
   generators don't repeat the trap that bit the e2e seed. (Same
   journal.)
3. **Rename the `_skipAuthRefresh` Axios flag.** It now conflates
   "don't retry-on-401" with "don't send Authorization". A clearer
   name (`_authEndpoint: true`) or a split into two flags would
   prevent the next person who reads `client.ts` from missing the
   dual meaning. (Same journal.)
4. **`HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`
   rename.** Seven cosmetic deprecation warnings from the Starlette
   constants module; entirely mechanical fix.
5. **Single source of truth for the IMPERSONATION TTL.** Currently
   duplicated between `LocalJwtProvider._DEFAULT_TTLS` and
   `admin/impersonate.py::_IMPERSONATION_TTL_SECONDS`. A drift would
   desync the SPA banner countdown from the actual token expiry.
   (Flagged in `260606-wu47-admin-clients-and-impersonate.md`.)
6. **Optional `reason` field on the impersonate UI.** The API
   accepts it; the SPA passes `null`. A two-step "enter support view?"
   dialog with an optional reason field is a small follow-up.
   (Flagged in `260606-wu54-admin-views-support-view.md`.)
7. **Single-title-writer assumption in `useSupportViewTitle`.** If a
   future per-route-title plugin lands, the `[SUPPORT] ` prefix needs
   to route through a single title-writer composable so the prefix
   re-applies. (Same journal.)
8. **The `production` GitHub Environment lacks required reviewers +
   branch constraint to `main`.** Flagged in
   `260605-wu61-oidc-federation.md` and re-flagged in
   `260605-wu63-deploy-pipeline.md` and
   `260605-wu66-drift-check-workflow.md`. Add before the prod
   cutover; the absence is currently safe only because no workflow
   targets that environment.
9. **Tighter RBAC on the UAMI.** Contributor on the RG is broad.
   Carve out Container Apps Contributor on the ACA env, Storage
   Blob Data Contributor on `$web`, Key Vault Secrets User on the
   vault, Reader elsewhere. (Flagged in
   `260605-wu61-oidc-federation.md` and reinforced by
   `260605-wu63-deploy-pipeline.md`'s SPA-upload 403 prerequisites.)
10. **`drift-check.yml` firing on push.** Root-cause and constrain to
    schedule + `workflow_dispatch` per
    `260605-wu66-drift-check-workflow.md`. Noise during the demo
    window is the immediate motivation; the design intent is the
    durable one.
11. **Curated set expansion from ~10 to ~50 docs.** The seed already
    handles any inventory size; only `fixtures.json` needs to grow.
    Re-run `scripts/fetch_fixtures.py` with `--target 50`. (Flagged
    in `260605-wu80-demo-corpus-expansion.md`.)
12. **IE-8064194 synthetic v2 pair violates
    `clauses_unique_path_per_version`** when staged against a real
    DB. The parser emits multiple leaves at the same path; either
    the parser dedupes or the staging path does. The 5-pair smoke in
    WU8.0 ran in `--dry-run` and never hit the DB.
    (`260605-fix-worker-staged-guard-and-env-validation.md`.)
13. **`seed_curated_set.py --teardown`** to un-park staged docs so
    post-demo development can resume without rebuilding the curated
    set from scratch. (Same journal.)
14. **Property-isolation test marker review.** Currently
    `@pytest.mark.nightly` only. Confirm the choice still matches
    the `addopts` exclusion logic six weeks post-demo. (Flagged in
    `260606-fix-property-isolation-strategy.md`.)
15. **Per-request observability of impersonation traffic.** Propagate
    `impersonator_id` through Principal / JWT claims / OTEL spans /
    per-request audit so logs distinguish impersonation calls from
    real client calls. (Flagged in
    `260606-wu47-admin-clients-and-impersonate.md`.)
16. **License file.** Closed-source / no-LICENSE is the demo default;
    a formal license decision (or staying with default copyright)
    needs to land before any wider public exposure.
17. **Six-week post-demo audit of the secfix commits.** Re-read each
    secfix diff ~2026-07-20 to confirm no regression has reintroduced
    the original issue. (Pre-committed in
    `260605-secfix-pattern-retrospective.md`.)
18. **Re-evaluate the secfix retrospective** against WU5.4's outcome:
    did the named-adversary framing prevent secfixes for that WU? If
    yes, the technique is validated; if no, the review-pass itself
    needs revisiting.

## Cross-reference: secfix-pattern retrospective

The canonical record of what was learned during this sprint about
prompt scaffolding for high-risk and integration surfaces is
[`260605-secfix-pattern-retrospective.md`](./260605-secfix-pattern-retrospective.md).
That entry catalogues six secfix events (five real security issues,
one hygiene), the three-iteration WU5.0 sequence that triggered the
analysis, and the two concrete prompt changes (named-adversary framing
+ explicit second-review constraint). It also captures the post-WU8.2-
hotfix extension of "high-risk surfaces" to include integration
surfaces (anywhere ≥3 components meet for the first time).

The two subsequent applications — WU4.7 and WU5.4 — both landed with
their adversary classes named in the journals (five classes on WU4.7,
six on WU5.4) and zero post-merge secfixes against either, which is
the early signal the technique works. The retrospective remains the
source of truth; this entry deliberately does not duplicate it.

## Acknowledgement

Built via the `engineering-team` skill driving parallel relayed
sessions through the worktree workflow documented in
`CLAUDE.md → CI / merge cadence`. Plan run dir:
`.engineering-team/runs/manual-20260604T151127Z/`. The skill's next
run should start by reading both this entry and the secfix
retrospective before scaffolding any new prompts — the cadence,
worktree hygiene, and adversary-framing patterns are baked into those
two documents and re-deriving them would be wasted context.
