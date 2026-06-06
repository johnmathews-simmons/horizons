# Horizons — Improvement plan

*Plan date: 2026-06-04. Baseline: docs 0–4 (post-fix) + the [evaluation report](evaluation-report.md) alongside this file.*
*Lead: engineering-team skill. Originally produced under `.engineering-team/runs/manual-20260604T151127Z/`; relocated here on 2026-06-06.*
*Posture: John (shipping owner) implements alongside subagents; lead-by-example. Each work unit is small enough to pick up in a single sitting.*

---

## Locked-in decisions for this plan

Captured from the evaluation + planning interview. Anything not pinned here is a planning question inside an individual work unit.

1. **Python 3.13, uv workspace, pytest, type annotations.** Per global CLAUDE.md.
2. **API framework: FastAPI** (uvicorn). Repository pattern over SQLAlchemy 2.0 async + psycopg3. Migrations: Alembic with `alembic_utils.PGPolicy` for RLS policies.
3. **SPA framework: Vue 3** + Vite + Pinia + Vue Router + @tanstack/vue-query + shadcn-vue (Reka UI) + Tailwind v4 + diff-match-patch.
4. **Auth: self-rolled JWT** with a pluggable `TokenProvider` Protocol (`LocalJwtProvider` now → `EntraIdProvider` later). PyJWT + argon2-cffi. Webapp uses access-token-in-memory + refresh-in-httpOnly-cookie; programmatic clients get JSON tokens.
5. **Database: Azure Database for PostgreSQL Flexible Server (PG 17).** No PgBouncer for the demo. Passwordless connection via managed identity in production.
6. **Blob storage: Azure Blob Storage.** Content-addressed keys (`originals/<sha256>.md`).
7. **Multi-tenant isolation: defence-in-depth (RLS + repository layer + lint-banned raw SQL + multi-user integration tests).** `SET LOCAL app.user_id` inside an explicit transaction bracket per request. Four Postgres roles: `schema_owner`, `api_app`, `ingestion_worker`, `admin_bypass`. Admin = two paths: `BYPASSRLS` (operator) + impersonation token (support). UUIDv7 PKs on private-state tables; serial PKs on corpus.
8. **Subscription model: normalised** — `subscriptions(id, user_id, valid_from, valid_to)` + `subscription_scopes(subscription_id, jurisdiction, sector)`. Watchlist ⊂ subscription enforced by service layer + INSERT/UPDATE trigger + RLS `WITH CHECK`. Reduction = soft-hide, append-only.
9. **Ingestion worker shape: open** — first work unit in the ingestion track is a spike to pick between long-running asyncio loop and ACA Job on cron.
10. **IaC: Bicep**, in `infra/` co-located with services. CI/CD: GitHub Actions, OIDC federation to Azure (no client secrets). Images to `ghcr.io/johnmathews/horizons`. Migrations run as a separate ACA Job before the API revision update. Rollback is `az containerapp update --image :sha-PREV` against the regressed app (revised 2026-06-06 from `activeRevisionsMode: Multiple` + traffic-weight flip — see `journal/260606-api-revisionmode-single.md`).
11. **SPA hosting: Storage `$web` + Azure Front Door Standard** (not Azure CDN — managed certs expired April 2026). Runtime `/config.json` for API URL + tunable thresholds.
12. **Observability: `azure-monitor-opentelemetry` distro → Application Insights** via ACA's managed OTEL agent. structlog for logs. Admin `/v1/admin/health/*` endpoints query Log Analytics with 60s cache.
13. **Repo: monorepo, uv workspace** — `core/` (shared models, repository layer, RLS plumbing, lawstronaut client) + `api/` + `worker/` as workspace members; `webapp/` outside the Python workspace.
14. **Confidence-suppression threshold default: 0.6**, tunable via admin UI (doc fix already applied).
15. **`effective_date` provenance: `publication_date + per_jurisdiction_default_lag`** for the demo; per-document overrides via admin UI; in-document commencement parsing is post-demo (doc fix already applied).

---

## Track structure

Eight tracks. Tracks 0 → 1 are strict prerequisites for everything else; 2, 3, 6 run in parallel after 1; 4 depends on 1+3 (data must exist); 5 depends on 4 (endpoints); 7+8 run alongside the back half.

| Track | Theme | Depends on | Critical path? |
|---|---|---|---|
| 0 | Repo scaffold + tooling + doc hygiene | — | Yes (blocks everything) |
| 1 | Tenancy spine (highest-risk, de-risked first) | 0 | Yes |
| 2 | Parser + alignment pipeline | 0 | Parallel to 1 |
| 3 | Ingestion worker | 1 (schema), 2 (alignment) | Yes |
| 4 | Public API — primitives + auth + private state | 1, 3 | Yes |
| 5 | SPA webapp | 4 (subset of endpoints) | Yes |
| 6 | IaC + CI/CD | 0; deploy stages need 4 | Yes |
| 7 | Observability + admin views + audit | 4 | Parallel to 5 |
| 8 | Demo prep — fixtures, smoke, docs, runbook | 4, 5, 6 | Yes (final) |

---

## Work units

Each work unit has: **WU number · title · depends-on · acceptance criterion** (what "done" looks like — verifiable by you or a subagent without ambiguity).

### Track 0 — Repo scaffold + tooling

**WU0.0 · Design-doc fixes.** Depends on: nothing. ✅ **Already complete in this session** (commit needed). Four edits to docs 2/3/4 addressing the atomicity claim, the RLS primary-vs-secondary disagreement, the confidence-threshold circular reference, and the `effective_date` provenance gap.

**WU0.1 · Initialise Python package layout.** Depends on: WU0.0. Acceptance: `uv` workspace with root `pyproject.toml` and members `core/`, `api/`, `worker/`. Each member has its own `pyproject.toml`, `src/<package>/__init__.py`, and `tests/`. `uv sync` succeeds. `python -c "import horizons_core, horizons_api, horizons_worker"` succeeds in the venv. Webapp scaffolded separately at `webapp/` with `npm create vue@latest` + Vite (Tailwind v4, TypeScript, Vitest, Pinia, Vue Router).

**WU0.2 · Linter + typechecker + pre-commit.** Depends on: WU0.1. Acceptance: `ruff check .` passes (with sensible defaults + the `flake8-tidy-imports` banned-api on `sqlalchemy.text` outside `core/db/session.py`). `mypy` or `pyright` strict on all `src/` modules. `pre-commit` runs ruff, mypy, end-of-file fixer, trailing whitespace. `pre-commit install` complete. Eslint + Prettier configured in `webapp/`.

**WU0.3 · pytest scaffolding + testcontainers.** Depends on: WU0.1. Acceptance: `pytest` runs and discovers tests; coverage configured; a `conftest.py` at root provides an `engine` fixture wrapping a `testcontainers` PG 17 instance; a smoke test that creates a temporary DB, runs an empty migration, asserts a `SELECT 1` returns. `uv run pytest -q` passes.

**WU0.4 · CI skeleton (`ci.yml`).** Depends on: WU0.2, WU0.3. Acceptance: `.github/workflows/ci.yml` triggers on PR + push to main + `workflow_dispatch`. Steps: setup uv, sync, ruff, mypy, pytest with coverage. Required check on branch protection. CI passes on an empty PR.

**WU0.5 · Repo hygiene.** Depends on: WU0.1. Acceptance: `.gitignore` covers `__pycache__`, `.venv`, `.coverage`, `htmlcov/`, `.engineering-team/`, `node_modules/`, `dist/`, `.env`. `README.md` short — points at docs 0–4. `LICENSE` decision noted in CLAUDE.md (closed-source demo; pick a license file before public exposure).

### Track 1 — Tenancy spine (THE highest-risk thing; built and exercised first)

**WU1.0 · Postgres role model.** Depends on: WU0.3. Acceptance: an Alembic migration creates the four roles (`schema_owner`, `api_app`, `ingestion_worker`, `admin_bypass`) with the documented privileges. `admin_bypass` has `BYPASSRLS`; `ingestion_worker` has `NOBYPASSRLS`; `api_app` has `NOBYPASSRLS`. Documented in `core/db/roles.md`.

**WU1.1 · Schema: users, subscriptions, subscription_scopes.** Depends on: WU1.0. Acceptance: Alembic migration creates `users(id UUIDv7 PK, email, password_hash, role enum('client','admin'), created_at)`, `subscriptions(id UUIDv7 PK, user_id FK, valid_from, valid_to, created_at)`, `subscription_scopes(subscription_id FK, jurisdiction text, sector text, PK on tuple)`. Indexes on `(user_id, valid_from)` and `(user_id, jurisdiction, sector)`. Append-only — no row updates allowed (enforced by trigger).

**WU1.2 · Schema: one private-state table (`watchlists`) + one corpus stub (`change_events`).** Depends on: WU1.1. Acceptance: `watchlists(id UUIDv7 PK, user_id FK, document_id, created_at, active bool default true)` and a stub `change_events(id bigserial PK, document_id, jurisdiction, sector, change_type, alignment_confidence, detected_at, effective_date)` with the doc-3 composite index `(jurisdiction, sector, detected_at, effective_date)`. Real corpus columns (clause text, before/after) are added in Track 3 — this stub is enough to exercise RLS.

**WU1.3 · `SECURITY DEFINER current_scope()` function + `app_private` schema.** Depends on: WU1.1. Acceptance: migration creates the `app_private` schema, revokes from `client`, creates `app_private.current_scope() RETURNS TABLE(jurisdiction text, sector text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''` returning the active subscription's scopes for `current_setting('app.user_id')::uuid`.

**WU1.4 · RLS policies on private-state + corpus tables.** Depends on: WU1.2, WU1.3. Acceptance: `watchlists` has RLS enabled + `FORCE` + `USING (user_id = current_setting('app.user_id')::uuid) WITH CHECK (...)`. `change_events` has RLS enabled + `FORCE` + a policy that filters via `(jurisdiction, sector) IN (SELECT * FROM (SELECT * FROM app_private.current_scope()) x)` for the `client` role. Policies live in `core/db/policies.py` as `alembic_utils.PGPolicy` objects and are versioned by Alembic autogen.

**WU1.5 · Connection layer + `SET LOCAL` request bracket.** Depends on: WU1.4. Acceptance: `core/db/session.py` exposes an `async_session_factory` and a `get_session()` async generator that opens a session, begins an explicit transaction, runs `SET LOCAL app.user_id`, `SET LOCAL app.user_role`, and (for clients) `SET LOCAL app.subscription_id`, yields the session, and commits on success / rolls back on error. SQLAlchemy `checkin` event runs `DISCARD ALL` on pool return. `text()` is the only allowed raw-SQL path and is permitted only inside this file (enforced by the WU0.2 lint rule + an architectural pytest).

**WU1.6 · Repository layer scaffold.** Depends on: WU1.5. Acceptance: `core/repos/base.py` defines a `Repository[T]` protocol. `core/repos/watchlists.py` implements `WatchlistsRepository` with `list_for(user_id)`, `create(*, user_id, document_id)`, `delete(*, user_id, watchlist_id)` — all with mandatory keyword `user_id`. `core/repos/change_events.py` implements scoped read methods that join the requesting client's scope. No `**kwargs` WHERE; methods return typed Pydantic models, not ORM rows.

**WU1.7 · Two-client integration test.** Depends on: WU1.6. Acceptance: `tests/isolation/test_private_state_isolation.py` and `tests/isolation/test_corpus_subscription_isolation.py` exist. Fixture `two_clients` creates user A with UK-only subscription and user B with EU-only subscription, each with its own `AsyncSession`. Tests assert: A's watchlist is invisible to B's `list_for`; A's watchlist returns 404 (not 403) for B's `get_by_id`; an EU change event is invisible to A and visible to B; admin bypass sees both. **This is the gate** — no Track 2/3/4 work merges until these tests pass and become a required CI check.

**WU1.8 · Property test for isolation.** Depends on: WU1.7. Acceptance: a Hypothesis test generates `(N clients × M subscriptions × K writes)` and asserts each client's reads ⊆ (their writes ∪ scope-allowed). Runs in CI on a separate `nightly` workflow (slow).

**WU1.9 · Admin operator + impersonation paths.** Depends on: WU1.6. Acceptance: `core/auth/admin.py` exposes `admin_operator_session()` (uses `admin_bypass` role) and `admin_impersonation_session(admin_id, target_user_id)` (uses `api_app` role with `SET LOCAL app.user_id = target` and `SET LOCAL app.impersonating_admin_id = admin`). Token minting writes one `admin_access_log` row per token. Integration test: an admin viewing client A's watchlist sees A's data, the log row is created, exit returns to admin's own context cleanly.

### Track 2 — Parser + alignment pipeline

Pure functions over `(markdown_v1, markdown_v2) → events`. No DB access. Tested against the 31 fixtures.

**WU2.0 · Clause-tree parser.** Depends on: WU0.3. Acceptance: `core/alignment/parser.py` parses a markdown document into a `Clause` tree using `markdown-it-py`. Each `Clause` has `path: list[str]`, `heading_text: str | None`, `body_text: str`, `numbering_label: str | None`. Heading-anchored markdown (Irish `PART N`, `**N.**`, `(N)`, `(a)`, `(i)`) produces nested nodes. Plain prose with inline numbering (Czech `Čl. I`, `N.`) is recognised by a configurable inline-pattern matcher. Tested against `ie-27732019-v1.md` (heading-anchored) and `cz-29662776-v1.md` (inline-numbered).

**WU2.1 · Generic structural recogniser + per-portal overrides.** Depends on: WU2.0. Acceptance: a default recogniser handles markdown-heading + the most common inline-numbering patterns. `parser_configs/<portal_slug>.yaml` allows per-portal overrides (regex for numbering, ordinal labels, separator handling). Loaded at startup. Tested against at least 5 jurisdictions from `data/samples/`.

**WU2.2 · Similarity stack.** Depends on: WU0.3. Acceptance: `core/alignment/similarity.py` exposes `shingle(text, k) -> set[str]`, `minhash(shingles, signature_size) -> list[int]`, `jaccard(a, b) -> float`, `lsh_candidates(signatures) -> Iterator[pair]` — using `datasketch`. All parameters (`k`, signature size, LSH bands, similarity threshold) read from a `TuningConfig` Pydantic model loaded from runtime config.

**WU2.3 · Alignment pipeline.** Depends on: WU2.0, WU2.2. Acceptance: `core/alignment/align.py` takes two clause trees and returns a list of `ChangeEvent(change_type, before_clause_uid, after_clause_uid, before_path, after_path, before_text, after_text, alignment_confidence)`. Passes 1 (source IDs — stub for now), 2 (heading-title + content), and 3 (content-similarity + monotonic DP). Tested with synthetic insert / delete / modify / move cases against the IE fixture (synthesised v2).

**WU2.4 · Alignment regression suite against the 31 fixtures.** Depends on: WU2.3. Acceptance: `tests/alignment/test_fixtures.py` aligns each fixture against a synthesised "no-change" duplicate (should produce only MOVED-or-empty events at confidence 1.0) and against synthesised mutations (one clause inserted, one deleted, one modified, one moved). Reports an aggregate "alignment-quality" score per fixture in CI output.

### Track 3 — Ingestion worker

**WU3.0 · Worker shape spike.** Depends on: WU0.3. Acceptance: a one-page decision doc at `docs/adrs/0001-worker-shape.md` documenting a small spike: implement both a long-running asyncio loop and an ACA Job stub against a fake schedule table, compare local-dev ergonomics, cost shape at demo scale, and operational complexity. Pick one. The rest of Track 3 builds on the chosen substrate.

**WU3.1 · Schema: documents, document_versions, document_poll_schedule, ingestion_incident.** Depends on: WU1.4. Acceptance: Alembic migration adds `documents(id, source_identifier, jurisdiction, sector, ...)`, `document_versions(id, document_id, version_no, content_hash, blob_url, publication_date, effective_date, valid_from, valid_to)`, `document_poll_schedule(document_id PK, cadence_interval, next_poll_at, last_polled_at, failure_count)`, `ingestion_incident(id, document_id, error_class, payload jsonb, occurred_at)`. Indexes per access pattern. `ingestion_worker` role has SELECT/INSERT/UPDATE on these tables and zero access to private-state.

**WU3.2 · Lawstronaut client + token-refresh seam.** Depends on: WU0.3. Acceptance: `core/lawstronaut/client.py` exposes `LawstronautClient` with `login()`, `refresh()`, `get_markdown(document_id)`, `list_jurisdictions()`, `list_portals()`. Token refresh is pre-emptive (refresh at 25 min into a 30-min TTL) with an in-process asyncio lock so only one refresh runs at a time. Stamina retries with exponential backoff on transient HTTP errors. Tolerates the documented field discrepancies (`content_markdown` vs `markdown`, string-or-number `document_id`, malformed `publication_date` ms). Tested against recorded responses (VCR or hand-rolled fixtures).

**WU3.3 · Schedule claim loop.** Depends on: WU3.0, WU3.1. Acceptance: implementation matches the spike's chosen substrate. Claims due rows via `SELECT ... FOR UPDATE SKIP LOCKED LIMIT N`, polls each, updates `next_poll_at`, increments `failure_count` on error. Kill-switch: if `failure_count > 5`, schedule entry is parked and an `ingestion_incident` is written. Liveness probe (worker shape permitting) on `/healthz`.

**WU3.4 · Per-document poll transaction.** Depends on: WU2.3, WU3.2, WU3.3. Acceptance: for each due document, the worker (a) fetches the markdown, (b) computes the content hash, (c) if unchanged extends the live version's `valid_to`, (d) if changed uploads the blob to `originals/<sha256>.md`, opens a Postgres transaction wrapping the version row + parsed clauses + alignment output + change events, commits. Failed runs leave at most one orphan blob; a periodic sweep job reclaims them.

**WU3.5 · Bootstrap script: seed the curated set.** Depends on: WU3.1. Acceptance: `worker/scripts/seed_curated_set.py` reads `data/samples/fixtures.json` + a `data/curated_set.yaml` (jurisdictions/sectors to poll), inserts `documents` rows, creates `document_poll_schedule` entries. Idempotent.

### Track 4 — Public API

**WU4.0 · Auth seam.** Depends on: WU1.5. Acceptance: `core/auth/provider.py` defines a `TokenProvider` Protocol with `issue_token(user_id, role, kind)`, `verify_token(token) -> Principal`, `revoke_token(jti)`. `core/auth/local_jwt.py` implements it with PyJWT (RS256, key in env / Key Vault), argon2-cffi password hash. JWT carries `sub`, `role`, `kind` (`access` | `refresh` | `impersonation`), `jti`, exp / iat. Refresh tokens recorded in a `refresh_tokens` table for revocation. Unit tests cover token forgery rejection, algorithm pinning (reject `none`, `HS256` when RS is configured), expiry, clock skew.

**WU4.1 · FastAPI app shell + auth middleware.** Depends on: WU1.5, WU4.0. Acceptance: `api/src/horizons_api/app.py` initialises FastAPI with: CORS, structured logging (structlog), the `get_session()` dependency, an `authenticated_user` dependency that verifies the bearer token and yields a `Principal`. Health endpoint `/healthz` returns 200 with no DB hit. Integration test: missing token → 401; invalid token → 401; valid token → 200 on a stub `/v1/me`.

**WU4.2 · `/v1/auth/login` + `/v1/auth/refresh` + `/v1/auth/logout`.** Depends on: WU4.0. Acceptance: login accepts `email + password`, returns `{access_token, refresh_token}` JSON for programmatic clients **and** sets the refresh token as `HttpOnly; Secure; SameSite=Lax` cookie for the webapp (one endpoint, decided by an `Accept` or `X-Client-Type` header — pick at implementation, document in `docs/api/auth.md`). Refresh exchanges a refresh for a new access (rotates the refresh, marks the old jti revoked). Logout revokes the active refresh jti. Tests cover all three flows.

**WU4.3 · `/v1/me` + per-user state CRUD (watchlists + saved_queries).** Depends on: WU4.1, WU1.6. Acceptance: `GET /v1/me` returns the user + their subscription summary. `GET/POST/DELETE /v1/me/watchlists` works through the repository layer. Watchlist write validates `document_id ∈ subscription_scope` at the service layer; trigger catches mismatches; integration test asserts a watchlist outside scope returns 422. `Cache-Control: private, no-store` on every per-user response.

**WU4.4 · The three primitives at all three scopes.** Depends on: WU3.4, WU4.1. Acceptance: `GET /v1/discovery`, `GET /v1/temporal`, `GET /v1/differential` each accept a `scope` parameter (corpus filter, document_id, or clause_id), return their respective shapes (identities + locations, timestamps, before/after content), support pagination on corpus-scope responses. Differential includes `before_text` / `after_text` only when `include_content=true` (defaults true at document/clause scope, false at corpus scope). All corpus reads go through scope-aware repository methods; an architectural test asserts no direct corpus-table access from `api/` outside the repository layer. p95 budget asserted by a load test against the seeded corpus (3-second target per doc 3).

**WU4.5 · Admin subscription endpoints.** Depends on: WU4.3, WU1.9. Acceptance: `GET/POST/PATCH /v1/admin/subscriptions` CRUD for any client (admin-only path under `/v1/admin/...`). Subscription reduction soft-hides out-of-scope watchlist rows (not delete). Tests cover the reduction path.

**WU4.6 · OpenAPI + API docs.** Depends on: WU4.4. Acceptance: FastAPI's auto-generated OpenAPI is reachable at `/openapi.json`. `docs/api/endpoints.md` is regenerated from the OpenAPI to stay in sync, or written by hand and kept current.

### Track 5 — SPA webapp (Vue 3)

**WU5.0 · Vue app shell + routing + auth store.** Depends on: WU4.2. Acceptance: `webapp/` Vite + Vue 3 + Vue Router + Pinia. `useAuthStore()` holds in-memory access token; refresh interceptor on Axios/Fetch retries 401s once via `/v1/auth/refresh` (the cookie). Login page works end-to-end against the local API. Tailwind v4 + shadcn-vue (Reka UI) configured.

**WU5.1 · Runtime `/config.json`.** Depends on: WU5.0. Acceptance: app boots by fetching `/config.json` containing `apiBaseUrl`, `tuningThresholds`, `featureFlags`. CI/CD generates this file per-environment; one bundle deploys everywhere.

**WU5.2 · Watchlist management view.** Depends on: WU4.3, WU5.0. Acceptance: client-role view to list / add / remove watched documents. Add-document modal lists documents within the user's subscription scope only. TanStack Vue Query for server state.

**WU5.3 · Change-browsing view + clause diff render (headline UX).** Depends on: WU4.4, WU5.0. Acceptance: a list view of recent change events (discovery + temporal data), each clickable to a detail view showing the clause diff. Diff renderer uses diff-match-patch against `before_text` / `after_text` from the API. Side-by-side default; toggle to unified. Alignment-confidence badge per change (raw 2-decimal float, red/amber/green by threshold). `MOVED` and below-threshold suppressed by default; toggleable. Tested with the IE fixture's synthesised changes from WU2.4.

**WU5.4 · Admin views.** Depends on: WU4.5, WU5.2. Acceptance: admin can list clients, view/edit their subscriptions, view system health (Track 7), enter a "support view" for any client. Support view shows a persistent amber banner (`bg-amber-500`) with "Support view — viewing CLIENT_NAME"; tab title prefix `[SUPPORT]`; explicit exit button. Audit log row written on entry.

**WU5.5 · Large-doc rendering safety.** Depends on: WU5.3. Acceptance: `@tanstack/vue-virtual` for the change-list view; a Web Worker for diff computation on documents > 1 MB to avoid main-thread blocking. Manual test against the 3.8 MB AL fixture.

**WU5.6 · Webapp CI build + lint.** Depends on: WU5.0. Acceptance: `.github/workflows/ci.yml` includes a webapp job that runs `pnpm install && pnpm lint && pnpm test && pnpm build`. Output goes to `webapp/dist/`.

### Track 6 — IaC + CI/CD

**WU6.0 · Bicep module skeletons.** Depends on: WU0.4. Acceptance: `infra/` contains Bicep modules for `network`, `keyvault`, `postgres-flex`, `storage` (originals + `$web`), `container-app-env`, `container-app-api`, `container-app-worker` (or ACA Job, per WU3.0), `application-insights`, `front-door`. A `main.bicep` composes them. `az deployment what-if` succeeds against a non-prod subscription.

**WU6.1 · OIDC federation GitHub → Azure.** Depends on: WU6.0. Acceptance: a user-assigned managed identity is provisioned (one-off); the GitHub repo's Settings → Environments has `staging` and `production` environments with federated credentials. CI can `az login --service-principal` via OIDC, no secrets stored.

**WU6.2 · `build-and-push.yml`.** Depends on: WU0.4. Acceptance: on push to `main`, builds `api/Dockerfile` and `worker/Dockerfile`, tags `:sha-<short>` and `:latest`, pushes to `ghcr.io/johnmathews/horizons-api` and `ghcr.io/johnmathews/horizons-worker`. SBOM generation optional but documented. Workflow has `workflow_dispatch` trigger.

**WU6.3 · `deploy.yml` with revision-based rollback.** Depends on: WU6.0, WU6.1, WU6.2. Acceptance: on push to main after build succeeds, runs `az deployment group create` against the target env to (a) ensure infra is current, (b) start a one-shot migrations ACA Job, (c) `az containerapp update` the API container app (Single mode: ACA creates a new revision, waits for readiness, shifts traffic, deactivates the previous), (d) post-shift smoke against the stable API FQDN as a tripwire, (e) `az containerapp update` the worker. Rollback is operator-driven: re-deploy the previous SHA (see `docs/runbooks/deploy.md`). SPA build job uploads `webapp/dist/` to `$web` blob container and purges Front Door cache for `index.html` + `config.json`. (Revised 2026-06-06 from the original blue/green sequence — see `journal/260606-api-revisionmode-single.md`.)

**WU6.4 · Migration ACA Job.** Depends on: WU3.1, WU6.0. Acceptance: a dedicated `migrate` ACA Job runs `alembic upgrade head` against the target DB using a managed-identity Postgres connection. Triggered by `deploy.yml` before traffic shift. Idempotent; safe to re-run; fails the deploy if migration fails.

**WU6.5 · Expand-contract migration policy.** Depends on: WU6.4. Acceptance: `docs/runbooks/migrations.md` documents the expand-contract rule (add column → deploy → backfill → deploy that uses it → drop in next deploy), including how RLS policy changes are handled (rolled out before the API code that depends on them). Reviewer checklist added to PR template.

**WU6.6 · Drift check workflow.** Depends on: WU6.0. Acceptance: `drift-check.yml` runs nightly: `az deployment what-if` against prod; reports any drift to a Slack channel (or GH issue). No auto-correction.

### Track 7 — Observability + admin views + audit

**WU7.0 · OpenTelemetry instrumentation.** Depends on: WU4.1. Acceptance: `core/observability/otel.py` initialises `azure-monitor-opentelemetry` (the official distro) with FastAPI auto-instrumentation, SQLAlchemy instrumentation, structlog correlation, and `otel-instrumentation-httpx`. Traces and metrics flow to App Insights via the ACA managed OTEL agent. Local dev exports to console.

**WU7.1 · structlog setup with FastAPI import ordering.** Depends on: WU4.1. Acceptance: structlog configured *before* FastAPI imports (the canonical trap). Logger emits JSON in prod, pretty in dev. Request context (request_id, user_id from middleware, trace_id from OTEL) included via processors.

**WU7.2 · Admin health endpoints.** Depends on: WU4.5, WU7.0. Acceptance: `GET /v1/admin/health/api` returns rate / p95 latency / error rate over the last 1h / 24h, fetched from Log Analytics via UAMI with a 60s in-process cache. `GET /v1/admin/health/ingestion` returns recent ingestion runs, current backlog, recent `ingestion_incident` rows. `GET /v1/admin/health/db` returns connection count, replication lag (n/a for now), top slow queries.

**WU7.3 · Alert rules.** Depends on: WU6.0, WU7.0. Acceptance: three Azure Monitor alert rules provisioned via Bicep: (1) API 5xx rate > 1% over 5 min, (2) API p95 latency > 3 s over 5 min, (3) ingestion failures > 3 in 1 h. Notifications to a webhook (Slack or your email).

**WU7.4 · Admin audit log surface.** Depends on: WU1.9, WU4.5. Acceptance: `GET /v1/admin/audit?since=...` lists `admin_access_log` rows with filtering. Admin-only. Immutable rows (no DELETE policy).

### Track 8 — Demo prep

**WU8.0 · Curated-set bootstrap for the demo.** Depends on: WU3.5. Acceptance: `data/curated_set.yaml` selects ~50 documents spanning ~10 jurisdictions and ~5 sectors. `seed_curated_set.py` populates `documents` + `document_poll_schedule` with appropriate cadences. A synthesised "v2" of at least 5 documents is staged so the demo has visible change events without waiting on Lawstronaut.

**WU8.1 · Two-client demo accounts + admin account.** Depends on: WU4.5. Acceptance: an admin CLI creates two demo clients (e.g. `demo-uk@example.test`, `demo-eu@example.test`) with disjoint subscriptions; one admin account. Documented in `docs/runbooks/demo-accounts.md` with copy-paste login snippets.

**WU8.2 · End-to-end smoke test.** Depends on: WU4.4, WU5.3, WU6.3. Acceptance: a Playwright test logs in as the UK client, browses the recent-changes view, opens a clause diff, asserts a `MODIFIED` event renders with before/after text and a confidence badge. Logs out, logs in as EU, asserts the EU view shows different events. Runs on every push.

**WU8.3 · Demo runbook.** Depends on: everything above. Acceptance: `docs/runbooks/demo.md` covers: pre-demo checklist (DB migrated, curated set seeded, accounts provisioned, smoke green), the demo script (login → browse → diff → switch clients → admin view → support view), recovery steps for common issues (API cold-start, Front Door cache, expired Lawstronaut token), public-exposure caveats (no client names, no real bank names — already in CLAUDE.md but restated here).

**WU8.4 · Journal entry + revise CLAUDE.md.** Depends on: everything. Acceptance: `journal/26xxxx-pre-demo-wrap.md` summarising the build, decisions taken vs deferred, things to watch during the demo, post-demo TODOs. CLAUDE.md updated with the now-existing `Commands` section (uv sync, alembic, pytest, dev server, container build).

---

## Dependency graph at a glance

```
Track 0 ──┬─→ Track 1 ──┬─→ Track 3 ──┬─→ Track 4 ──┬─→ Track 5 ──┐
          │             │             │             │             │
          ├─→ Track 2 ──┘             │             │             ├─→ Track 8
          │                           │             │             │
          └─→ Track 6 ────────────────┴─→ deploy ───┘             │
                                                                  │
                                      Track 4 ──→ Track 7 ────────┘
```

Critical path: 0 → 1 → 3 → 4 → 5 → 8. Track 2 parallelises off the critical path. Track 6 runs alongside Tracks 1–5 (infra ready by the time deploy is needed). Track 7 runs alongside Track 5.

---

## Suggested pickup order for solo / lead-by-example sessions

If you're picking up work units alone in sittings:

1. **WU0.0 → WU0.5** in one session (a couple of hours; finishes the scaffold).
2. **WU1.0 → WU1.7** in two-to-three focused sessions. WU1.7 is the moment the tenancy spine is real and provably correct — celebrate it.
3. WU2.0 → WU2.3 in parallel as a separate workstream (parser is satisfying; can run in evenings).
4. WU3.0 (spike) standalone — small but important.
5. **WU4.0 → WU4.4** is the bulk of the API. Pace yourself.
6. WU6.0 → WU6.4 in one or two sessions once you have a callable API.
7. Track 5 (Vue webapp) feels different from the rest — schedule it when you want a context switch.
8. Track 7 + Track 8 sprint to the demo.

Each WU is sized to be a single session's work for one engineer on a normal day.

---

## Open items intentionally deferred

These do NOT block the demo. Tracked here so they don't get lost.

1. Per-poll metrics aggregation pattern (raw rows vs rollup) — decide after observing polling volume.
2. Read replica trigger — add when p95 latency justifies.
3. Notification service (email / webhook on change events) — explicitly out of demo scope.
4. Self-service signup, federated SSO — out of demo scope.
5. Cross-document near-duplicate detection — MinHash signatures are persisted; the surface comes later.
6. In-document commencement parsing for `effective_date` precision — placeholder lag-based formula is sufficient for the demo.
7. Per-document subscription overrides, time-window-limited subscriptions — start with the simple cross-product.
8. Manual override / annotation of clause alignments — post-demo.
9. License file — needed before public exposure.
10. ACR migration from GHCR — post-demo if managed-identity pulls become friction.
