# Horizons — Architecture evaluation report

*Evaluation date: 2026-06-04. Project state: pre-code, post-design (docs 0–4).*
*Lead: engineering-team skill, 5 parallel subagents. Originally produced under `.engineering-team/runs/manual-20260604T151127Z/`; relocated here on 2026-06-06.*

---

## Executive summary

1. The design chain (docs 1–4) is unusually strong on **invariants** — clause identity, two-axis multi-tenant isolation, append-only history — and unusually thin on **mechanism**. Build planning has to translate roughly 15 architectural principles into ~40 concrete decisions before any code lands; this report enumerates them.
2. Multi-tenant isolation is the load-bearing thing to get right and is also where production incidents are most likely to come from. The single highest-impact decision is **how `app.user_id` is set per request** — get this wrong and RLS evaluates against a recycled connection's previous tenant. Subagent consensus on the safe pattern is clear (`SET LOCAL` inside an explicit transaction bracketing the whole handler) and it must be the **first** thing built and exercised by integration tests.
3. Two design-doc claims need correction before code: (a) doc 4 line 97 asserts atomic transactions spanning Postgres + Azure Blob, which the substrate cannot provide; (b) doc 3 principle 8 and doc 4 §Public API/How disagree on whether RLS is the primary corpus filter or a belt-and-braces second layer. Both are noted below as design-doc fixes for Phase 2.
4. The team is unanimous on most concrete framework choices (FastAPI + SQLAlchemy 2.0 async + Alembic + Bicep + React/Vite + shadcn/ui). One genuine disagreement: ingestion worker shape (long-running asyncio loop vs ACA Job cron) — both are defensible; pick in planning.

---

## Test Suite Results

Pre-code project — no test suite to run, no linter configured. Repo contains only `docs/`, `data/samples/`, and one ad-hoc script (`scripts/fetch_fixtures.py`). Coverage: N/A. **This is itself a finding: a pre-code repo for a production-grade target needs the test and lint scaffolding to be the first build artifact, not bolted on later** (work unit candidate in Phase 2).

---

## Project overview

Horizons is a regulatory-change intelligence service for large multinational banks: it watches public legal sources via the Lawstronaut API and surfaces upcoming legal changes — clause-level diffs between document versions — so clients have lead time before changes are in force.

**Architecture (planned, pre-code):**

1. **Ingestion worker** — scheduled poller against Lawstronaut; for each curated document checks content hash, runs clause-alignment pipeline (shingling + MinHash + LSH + monotonic DP) when changed, writes `change_events` rows. Corpus-global; knows nothing about tenants.
2. **Public REST API** — single HTTP surface for all clients (webapp + programmatic). Exposes three primitives (discovery, temporal, differential) at three scopes (corpus, document, clause). Authenticates with bearer JWT; resolves token to `(user_id, role, subscription_scope)`; enforces two-axis multi-tenant isolation via Postgres RLS + repository pattern + lint-banned raw SQL + multi-user integration tests.
3. **SPA webapp** — static bundle from Azure Blob Storage + CDN. A customer of the same public API. Headline UX: clause-level before/after diff with alignment-confidence affordances.

Shared substrate: Azure Database for PostgreSQL Flexible Server + Azure Blob Storage. Target deploy: Azure Container Apps, CI/CD via GitHub Actions, declarative IaC (Bicep recommended), images to `ghcr.io/johnmathews/horizons`.

---

## Strengths

1. **The identity model in doc 2 is the strongest part of the design.** `clause_uid` (stable, carried across versions by alignment) vs `clause_path` (positional, renumbers freely), with cheaper-then-fuzzier alignment (source IDs → heading+content → content-similarity under monotonic constraint), is exactly right for the substrate. Honest about limitations (boilerplate collisions, large restructurings).
2. **Two-axis isolation framing.** Treating subscription leakage with the same severity as cross-tenant leakage is the correct B2B-bank framing; defence-in-depth (RLS + repository + lint-banned raw SQL + multi-user integration tests) is the textbook answer. Few startup-grade designs commit to all four layers up front; this one does.
3. **Append-only / clause-granular precomputed `change_events`.** Doc 3 principle 3 — never compute diffs at query time — is the right call for the 3-second p95 budget on heavy corpus queries.
4. **Honest open-questions footers.** Every doc tracks what is still in flight rather than papering over it. Doc 0's framing of the chain as a linked RFC with complementary ADR-style honesty is well-executed and rare. Resist closing these performatively as planning progresses.
5. **"Configuration over code" extended to tuning parameters.** Doc 2's commitment to runtime-tunable shingling *k*, signature size, similarity / confidence thresholds (with UI surface) avoids the slow redeploy-to-experiment trap that kills most demo iteration cycles. Keep it.
6. **Clean separation of three deployable services with explicit non-responsibilities.** Each service has a "Does" and "Doesn't" section in doc 4; the SPA is architecturally a client of the same public API external customers use, with no internal back-channel. Forces tenant boundaries to be exercised by your own UI every day.

---

## Weaknesses

1. **Mechanism is under-specified relative to principle.** Doc 4 names principles and services but no endpoint inventory. ~20–40 endpoints' worth of detail will compound ambiguity in planning unless an `endpoints.md` companion to doc 4 is produced first. (See gap #1 below.)
2. **Schema is deferred** (doc 3 line 5). Tables are named but no columns, PKs, FKs, or index choices. The DDL itself is a planning deliverable — but the load-bearing schema choices (e.g. `effective_date` provenance, subscription model shape) are higher-stakes than "writing SQL" and must be decided explicitly.
3. **`effective_date` is indexed in doc 3 line 33 but never defined.** The product's entire "horizon" framing — *upcoming legal changes, lead time before in force* — rides on this column. Lawstronaut surfaces `publication_date` only (with malformed milliseconds, per operational-notes). Provenance must be settled before any change event is materialised.
4. **Confidence-suppression default is a circular reference.** Doc 2 line 134 defers to doc 3; doc 3 line 67 lists it as open. No starting value anywhere. The demo's headline clause-diff moment cannot ship without one — pick a defensible empirical default (e.g. 0.6) and refine.
5. **Watchlist ⊂ subscription enforcement layer unspecified.** Doc 4 asserts the rule twice (lines 50, 108) but never says whether it's RLS, CHECK, trigger, or application-layer. Postgres CHECK can't reference another table; subagent recommendation is service-layer validation + INSERT/UPDATE trigger + RLS `WITH CHECK` as belt-and-braces.
6. **Admin bypass mechanism unresolved.** Doc 4's open question (impersonation vs role bypass) blocks corpus-table policy authoring. Recommended: **both** — `BYPASSRLS` for operator/system-health views, impersonation token for support views (the support view path exercises the same client RLS code path, so isolation regressions surface fast).
7. **Tuning parameters are global, not per-client.** Runtime-tunable similarity thresholds (doc 2 line 125) affect all clients simultaneously. Whether tuning re-runs alignment on past versions or just filters precomputed events post-hoc is undecided; the silent-wrong-choice in a tuning UI is dangerous.

---

## Assessment dimensions

Scores reflect the design baseline as it stands, not what the implementation will be.

1. **Simplicity: 4/5.** The three-service split is clean. The defence-in-depth layering is justified by the cost of getting tenancy wrong, not over-engineering. Slight deduction for the documented duplication of clause text (blob + inline row), which is deliberate but undocumented as duplication — a future reader will try to "fix" it.
2. **Robustness: 3/5.** Strong on the design-level invariants. Loses points for: (a) doc 4's false atomicity claim across Postgres + Blob; (b) no specification of poison-pill handling beyond `SKIP LOCKED` (3.8 MB AL outlier and 20 MB anticipated outliers will time out or OOM); (c) no specified retry / backoff / kill-switch on the ingestion path beyond a hand-wave.
3. **Security: 4/5.** Two-axis isolation is architecturally correct. Defence-in-depth is the right posture. Loses one point because **most production tenancy incidents will come from implementation details the docs don't yet pin down** (PgBouncer + `SET LOCAL` interaction, async-pool transaction-bracket discipline, secret rotation, admin audit log). Documented as principles but not as mechanism.
4. **Flexibility: 5/5.** Configuration over code is committed-to at every layer (taxonomies, polling cadence, tuning thresholds, sources, jurisdictions). Adding a portal is a data change. New endpoints inherit the same scoping. The shape is friendly to extension without retrofitting.
5. **Test coverage: N/A** (no code exists). The two-axis multi-tenant test scaffolding is asserted by doc 4 but the pattern is unspecified. Subagent recommendation: testcontainers Postgres 17 + two-AsyncSession fixture + Hypothesis property test, isolation tests as required CI check.
6. **Documentation accuracy: 4/5.** Docs are coherent end-to-end; the linked-RFC chain with open-questions footers is well-executed. Two correctable inaccuracies: false atomicity claim (doc 4 line 97) and the doc-3/doc-4 disagreement on RLS-as-primary-vs-secondary for corpus scoping.
7. **Documentation completeness: 3/5.** Complete on principles, services, taxonomies. Incomplete on: endpoint inventory, schema DDL, framework choices, observability stack, JWT key rotation, admin audit log shape, subscription change semantics, ingestion incident handling, and the parser config for portal-specific clause structures. Most of these are appropriate for Phase 2; the question is which become design-doc updates vs ADRs vs code-only.
8. **Deployment quality: N/A** (no code, no Dockerfile, no CI). The user's brief commits to Azure Container Apps with declarative IaC, revision-based rollback, Postgres via managed identity, and full HTTP-shaped observability — this is the deployment plan, but none of it exists yet. Phase 2 produces the IaC + workflow blueprints.

---

## Design bugs (the equivalent of "bug candidates" for a pre-code project)

These are concrete design errors in the docs — actionable as doc edits.

1. **[VERIFIED] False atomicity claim, `docs/4. services.md` line 97.** "Each per-document poll is a self-contained Postgres transaction. Either everything for that document (hash check, version row, blob upload, alignment, change events) commits, or none of it does." Blob upload is not transactional with Postgres. Fix: switch to **upload-then-commit with content-addressed blob naming (`originals/<sha256>.md`) + orphan sweeper**. The PG transaction wraps `(hash, version row, clause rows, change_events)`; the blob upload happens before COMMIT, the URL in the version row references a content-hash path that becomes reachable only once the row is visible.
2. **[VERIFIED] Doc 3 principle 8 vs doc 4 §Public API/How disagree.** Doc 3 line 38 says corpus-side enforcement *is* the security-definer function; doc 4 line 128 says the repository join is primary and RLS is "the second layer." Pick one: subagent recommendation is **doc 4's framing** (repository join is primary; RLS is the safety net for missed joins). Update doc 3 principle 8 to match.
3. **[VERIFIED] Confidence-suppression default is a circular reference** (doc 2 line 134 → doc 3; doc 3 line 67 → open). Pin a starting empirical value (0.6 is a defensible starting point for shingling-based similarity in legal-prose corpora). Tunable via UI per the existing commitment.
4. **[VERIFIED] `effective_date` indexed but undefined** (doc 3 line 33). Decide its provenance: Lawstronaut `publication_date` (the only date field surfaced today) ≠ effective date in most jurisdictions. Three plausible options: (a) inherit `publication_date` as a placeholder and accept that "horizon" framing is approximate at demo time; (b) parse the in-document text for an effective-date phrase per jurisdiction (expensive, brittle); (c) maintain a per-jurisdiction default lag (e.g. "effective 30 days after publication unless overridden"). Pick one and write it down before any change event row is materialised.

---

## Gap analysis — concrete decisions Phase 2 must make

Reorganised from subagent reports; numbered for reference.

### Framework / library choices (consensus across subagents unless flagged)

1. **API framework: FastAPI** + uvicorn (runner-up: Litestar — defer; perf gap doesn't matter at demo scale, ecosystem matters more).
2. **DB driver: psycopg3 async.**
3. **ORM/query layer: SQLAlchemy 2.0 async** (Core + selective ORM). Repository layer wraps it.
4. **Migrations: Alembic** with `alembic_utils.PGPolicy` (or `DelfinaCare/rls`) for RLS policy management. RLS policies are versioned migrations from day one.
5. **Connection pooling: SQLAlchemy `AsyncAdaptedQueuePool` in-app, no PgBouncer for the demo.** If PgBouncer is enabled later it must be transaction-pool mode only; session-pool mode silently breaks `SET LOCAL`.
6. **HTTP client (Lawstronaut): httpx async + stamina** for retries (opinionated over tenacity; structlog/Prometheus hooks built in).
7. **Markdown parser for clause tree: `markdown-it-py`** (handles both Irish heading-anchored and Czech inline-numbered substrates with a fallback structural recogniser).
8. **Similarity stack: `datasketch`** for MinHash + LSH; shingling rolled directly.
9. **JWT library: PyJWT.** Password hashing: **argon2-cffi.** *Do not use passlib* (broken on Python 3.13 per subagent #3).
10. **SPA framework: React 19 + Vite** + shadcn/ui + Tailwind v4 + TanStack Router + TanStack Query + Zustand.
11. **Diff renderer (SPA): `diff-match-patch` client-side** against `before_text` / `after_text` returned by the API.
12. **Logging: structlog** (with the FastAPI-import-ordering trap called out in subagent #3).
13. **Observability: `azure-monitor-opentelemetry` distro** → Application Insights via ACA's managed OTEL agent. No Prometheus sidecar.

### Infrastructure / deployment

14. **IaC: Bicep** (not Terraform). Lives in `infra/` co-located with services. No state file; deployment history via ARM activity log; `what-if` posted on infra PRs.
15. **ACA topology:** one Container Apps environment per logical env (`cae-horizons-demo` / `-prod`). API external ingress; worker no ingress.
16. **Worker shape: GENUINE DISAGREEMENT — pick in Phase 2.**
    - Subagent #3: long-running asyncio loop with `SELECT FOR UPDATE SKIP LOCKED` (rejects ACA Jobs as adding deployment complexity for a workload that's mostly idle anyway).
    - Subagent #4: **ACA Jobs on a 15-minute cron** — polling is intrinsically cron-shaped and Jobs match doc 4's "scheduled worker" framing.
    - Both are defensible; the ACA Jobs path simplifies operational story and resource billing, the long-running container simplifies local dev. Decide before infra code lands.
17. **Postgres connection: passwordless via Entra/managed identity** (no static DB credential in Key Vault).
18. **Migrations run in a separate ACA Job before traffic shift** (not in API startup hook — multi-replica race; not in CI — firewall pain).
19. **GHCR auth for ACA pull:** PAT in Key Vault initially; ACR + managed identity post-demo.
20. **Revision-based rollback:** `activeRevisionsMode: Multiple` + `az containerapp ingress traffic set` (atomic). CI-automated *and* operator-manual paths.
21. **SPA hosting: Storage `$web` + Azure Front Door Standard** (not Azure CDN Standard — managed certs expired April 2026 per subagent #4).
22. **SPA runtime config: `/config.json` fetched at boot** (not build-time env vars) — one bundle serves dev/staging/prod and matches the runtime-tunable-config requirement.

### Schema and tenancy decisions (must be locked before any RLS code)

23. **`app.user_id` plumbing: `SET LOCAL` inside an explicit transaction** bracketing the whole handler, dispatched by a FastAPI dependency. SQLAlchemy `checkin` event emits `DISCARD ALL` as belt-and-braces.
24. **Four Postgres roles:** `schema_owner` (Alembic, out-of-band), `api_app` (request-time), `ingestion_worker` (writes corpus, zero access to private-state — `REVOKE ALL`), `admin_bypass` (operator-mode admin only). API never connects as superuser or schema owner.
25. **Admin mode = two connection paths:** `BYPASSRLS` role for operator views; impersonation token (`SET LOCAL app.impersonating_admin_id`) for support views. Impersonation reuses client RLS — one audit row per token mint.
26. **Subscription model: normalised, not arrays.** `subscriptions(id, user_id, valid_from, valid_to)` + `subscription_scopes(subscription_id, jurisdiction, sector)`. B-tree on `(user_id, jurisdiction, sector)`. Subscription reduction = soft-hide watchlist rows (append-only).
27. **SECURITY DEFINER `app_private.current_scope()`** with `STABLE` + `search_path = ''`, wrapped `(SELECT … )` so the planner evaluates once per query. Functions in `app_private` schema, revoked from client role.
28. **UUIDv7 primary keys on every private-state table** (no serial-ID write-rate side channel). Corpus tables keep serials.
29. **All per-user responses: `Cache-Control: private, no-store`.** Code-review checklist item for any future Redis cache: keys MUST include `user_id`.
30. **404 (not 403) for any "out of scope" or "not yours" case** in corpus and private-state surfaces. PK lookups on corpus tables run scope predicate in the same `WHERE` (no timing side channel).
31. **Lint-banned raw SQL:** Ruff `flake8-tidy-imports` banned-api on `sqlalchemy.text` outside `app/db/session.py` + pre-commit grep + architectural pytest asserting no `app/api/**` module transitively imports `sqlalchemy.text`.

### Auth, observability, audit

32. **Pluggable auth seam:** `TokenProvider` Protocol with `LocalJwtProvider` for now → `EntraIdProvider` later. Methods: `issue_token`, `verify_token`, `revoke_token`. Concrete shape sketched in subagent #3.
33. **Refresh tokens: webapp = `HttpOnly; Secure; SameSite=Lax` cookie**, access token in memory; programmatic clients get JSON token. **This tightens doc 4's "stores the token client-side for the session" — needs a doc edit** to acknowledge two delivery modes.
34. **Admin-as-support audit log:** `admin_access_log(admin_user_id, target_user_id, endpoint, ts, impersonation_token_id)`, FK-immutable, one row per token mint and one per request. Live in the same Postgres until a write-only sink is justified.
35. **Observability surface for admin UI:** `/v1/admin/health/*` endpoints query Log Analytics via UAMI with 60s cache; three demo-scope alert rules in Azure Monitor (high error rate, high p95 latency, ingestion failures).

### Repo layout and tooling

36. **Monorepo, uv workspace:** `core/` (shared models, repository layer, RLS plumbing) + `api/` + `worker/` as workspace members; `webapp/` outside the Python workspace. One `pyproject.toml` per service + root.
37. **Linter + typechecker scaffolding as the first build artifact**: `ruff`, `mypy`/`pyright` configured before any service code lands. Pre-commit hooks. Per global CLAUDE.md.
38. **Sample-fixture-driven test suite:** the 31 fixtures in `data/samples/` are the regression substrate for the parser + alignment pipeline. Reuse `data/samples/fixtures.json` as the test inventory.

### Open questions inherited from docs

39. Per-poll metrics aggregation pattern (raw rows vs rollup) — defer until polling volume is known.
40. Read replica trigger threshold — defer until observed latency justifies.
41. Subscription change semantics on reduction — recommendation above (soft-hide watchlist), but needs sign-off.

---

## Architectural assessment by subsystem

### Multi-tenant isolation
**Right shape.** Two-axis framing, defence-in-depth layering, explicit threat-equality between private-state leakage and subscription leakage. **Production risk lives in the implementation seam between async pool, transaction bracket, and `SET LOCAL` GUC.** This is where the team will spend 80% of its tenancy-bug-fix time. De-risk first: lock in pattern, write the two-AsyncSession integration test, exercise it before any other feature.

### Ingestion pipeline
**Right shape on alignment** (cheaper-then-fuzzier, monotonic constraint, persistent MinHash signatures, explicit confidence scoring). **Wrong shape on transactional atomicity** (blob + Postgres can't be one transaction; design-doc fix above). **Underspecified on failure handling** (`SKIP LOCKED` doesn't address poison-pill docs; need failure counter + backoff + kill-switch + `ingestion_incident` wired in). The clause parser handling two genuinely different substrates (heading-anchored markdown vs inline-numbered prose) is a long-tail config-burden — recommend a generic structural recogniser as the default with per-portal overrides, not per-portal-only.

### Public API
**Right shape on principles** (single surface, role-driven, scoped). **Endpoint inventory missing** — Phase 2 must produce one. **3-second p95 vs corpus-wide differential queries is plausibly violated at large result sets** under p99 clause sizes in 5 MB documents; requires deliberate index design and tight LIMITs. Self-rolled JWT seam (pluggable to Entra later) is the right call; the `AuthBackend`/`TokenProvider` protocol must be drawn now, not retrofitted.

### Webapp
**Right shape** (architecturally a client, no privileged channel). Headline clause-diff UX is technically risky for large documents (3.8 MB AL outlier, anticipated 20 MB outliers) — `@tanstack/react-virtual` + Web Worker for diff is the recommended mitigation. Admin support view needs a persistent visual marker (amber banner, tinted chrome, `[SUPPORT]` tab prefix, explicit exit) so the user never silently impersonates.

### Deployment / CI/CD
**Right shape** (declarative IaC, GHCR images, revision-based rollback). **Worker-as-Job vs worker-as-container is genuine disagreement** (#16 above). Migrations as a separate ACA Job before traffic shift is the safe pattern. Passwordless Postgres via managed identity strengthens the tenancy story by removing a static credential entirely. Demo-scope cost estimate ~$95/mo (subagent #4); ~$15 to keep API `min=1` and avoid cold-start at first impression.

---

## Sequencing recommendation for Phase 2 (build order)

1. **Repo + tooling scaffold first** — uv workspace, ruff, mypy, pytest, pre-commit, GitHub Actions CI skeleton. The CI gate exists before any service code.
2. **Tenancy spine second** — Postgres schema with `users`, `subscriptions`, `subscription_scopes`, one private-state table (`watchlists`), one corpus-shaped stub (`change_events`). RLS policies on both. Four Postgres roles. `SECURITY DEFINER current_scope()`. `SET LOCAL app.user_id` plumbing through FastAPI dependency. Repository layer. Lint-banned raw SQL. **Two-client integration test asserting isolation on both axes.** This is the minimum-viable safety net.
3. **Parser + alignment third (parallel)** — pure functions over `(markdown_v1, markdown_v2) → events`, tested against the 31 fixtures. Independently buildable; can begin as soon as #2 has stub tables.
4. **Ingestion worker fourth (parallel)** — Lawstronaut auth + token-refresh harden (critical-path; 30-min token TTL + 50 docs); schedule table; `SKIP LOCKED` claim loop or ACA Job (decide #16); upload-then-commit pattern; `ingestion_incident` wired in.
5. **Three primitive endpoints fifth** — discovery, temporal, differential at the three scopes. Re-uses the tenancy spine and the alignment outputs.
6. **SPA sixth** — diff render spike in parallel against static fixtures; rest after endpoints stabilise.
7. **IaC + CI/CD deploy pipeline throughout** — start with Bicep modules + `ci.yml` early; `deploy.yml` + revision shift wired in once the API is callable end-to-end.
8. **Admin views and health/coverage endpoints last** — not on the demo headline path.

---

## Sources (de-duplicated across subagents)

- [PG 17 Row-Level Security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)
- [Postgres RLS Footguns — Bytebase](https://www.bytebase.com/blog/postgres-row-level-security-footguns/)
- [PostgreSQL Row Level Security — Daniel Imfeld](https://imfeld.dev/notes/postgresql_row_level_security)
- [Supabase RLS performance and best practices](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv)
- [RLS sounds great until it isn't — PlanetScale](https://planetscale.com/blog/rls-sounds-great-until-it-isnt)
- [Row-Level Security with SQLAlchemy and Alembic — Adriano Vieira](https://www.adrianovieira.eng.br/en/posts/architecture/row-level-security-sqlachemy-alembic-guide/)
- [DelfinaCare/rls — SQLAlchemy + Alembic RLS](https://github.com/DelfinaCare/rls)
- [alembic_utils](https://github.com/olirice/alembic_utils)
- [Litestar vs FastAPI — Better Stack](https://betterstack.com/community/guides/scaling-python/litestar-vs-fastapi/)
- [SQLAlchemy 2.0 pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
- [PgBouncer Configuration and Best Practices — Heroku](https://devcenter.heroku.com/articles/best-practices-pgbouncer-configuration)

Subagent reports under `discussions/` carry the full source lists.
