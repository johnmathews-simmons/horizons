# Architecture and gaps — review of Horizons design baseline

*Last revised: 2026-06-06.*
*Path: docs/plan/discussions/01-architecture-and-gaps.md.*

*Reviewer: Product Owner / architecture. Date: 2026-06-04. Baseline: docs 0–4, api/, journal 260604, memory.*

Findings labelled **[VERIFIED]** (cite-backed) or **[SUSPECTED]** (inferred). Quotes are direct; line numbers anchor citations.

---

## A. Internal consistency

1. **[VERIFIED] Doc 3 principle 8 and doc 4 §"Public API service / How" disagree on what RLS guards on the corpus side.** Doc 3 line 38: "subscription-scope filtering on corpus reads via a security-definer function that joins to the requesting client's subscription." Doc 4 line 128: "An RLS policy on the corpus tables provides the **second layer**, using a security-definer scope function." Doc 3 implies the security-definer function *is* the filter; doc 4 places the repository-side join as primary and RLS as belt-and-braces. Different failure modes (a missing join in repo is caught by RLS only under the doc-4 reading). Pick one.

2. **[VERIFIED] Admin bypass mechanism unresolved.** Doc 4 line 128 says admin "bypasses subscription policies (its own audited code path)" but never says how. Open question line 176 names the choice (impersonation vs role bypass) without resolution. Until pinned, the RLS policy on corpus tables can't be authored — does `admin` connect as a Postgres role with `BYPASSRLS`, or does the policy check `current_setting('app.role') = 'admin'`? Different threat models.

3. **[VERIFIED] Watchlist-subset-of-subscription enforcement layer unspecified.** Doc 4 lines 50, 108 assert the rule twice; neither says whether enforcement is RLS, CHECK constraint, trigger, or application-layer. RLS won't naturally do cross-table predicates; a trigger or join-based CHECK is needed. Tied to open question line 180 (subscription reduction → existing watchlists?).

4. **[VERIFIED] Confidence-suppression default is a circular reference.** Doc 2 line 134 defers to doc 3; doc 3 line 67 lists it as open. No starting value anywhere. The demo's headline clause-diff moment depends on this threshold.

5. **[VERIFIED] Doc 3 principle 5 (blob-only markdown) + doc 4 ingestion (inline clause text) duplicate clause text by design.** Deliberate per principle 5's rationale, but undocumented as duplication — a future reader will try to "fix" it.

6. **[VERIFIED] Doc 4 line 97 claims atomic transactions across Postgres + Blob.** "Either everything for that document (hash check, version row, blob upload, alignment, change events) commits, or none of it does." Blob upload isn't transactional with Postgres. Standard pattern: upload-blob-first with content-hash key, reconcile on retry. The doc claims atomicity the substrate doesn't provide.

7. **[VERIFIED] "API responsiveness is non-negotiable" + shared Postgres is in tension.** Doc 4 lines 56–63. Doc 3 line 66 owns the read-replica open question. The non-negotiable claim is stronger than the substrate supports until a replica exists.

8. **[VERIFIED] Auth design hasn't decided on refresh tokens.** Doc 4 line 160 mentions "(and optionally a refresh token)" with no decision. Given today's "self-rolled JWT, pluggable seam for Entra later" call, pin: pure short-lived JWT vs JWT + refresh (revocation list, longer TTL, different storage).

9. **[SUSPECTED] Live-tuning of similarity thresholds during the demo affects all clients simultaneously.** CLAUDE.md and doc 2 line 125 commit to runtime-tunable thresholds, but doc 4 never says whether tuning re-runs alignment for past versions or just filters precomputed events post-hoc. Either choice is defensible; the silent wrong choice in a tuning UI is dangerous.

10. **[SUSPECTED] Polling delivery shape unspecified.** Doc 1 line 46 says discovery is "suitable for polling" but no doc defines HTTP polling cadence, SSE, or WebSocket, nor per-tenant rate limits. Relevant with 50+ live-polled docs.

---

## B. Gaps that block implementation

11. **[VERIFIED] No web framework chosen.** Doc 4 line 124 ("Thin HTTP layer over Postgres") is a posture, not a choice. Candidates: FastAPI (mature, Pydantic v2, OpenAPI), Litestar (faster via msgspec, batteries-included middleware), Django REST (overkill). Recommend FastAPI; the 2× Litestar perf delta won't matter at demo scale and ecosystem matters more.

12. **[VERIFIED] No ORM / driver chosen.** Schema deferred (doc 3 line 5). RLS + tenant-scoping has well-known SQLAlchemy pitfalls. Options: raw `psycopg3` + thin helper (simplest, fits "easy to understand"); SQLAlchemy 2.0 async with `before_cursor_execute` to set `SET LOCAL app.user_id`; SQLModel (weakens migrations). Pick before schema lands.

13. **[VERIFIED] Migration tool unspecified.** Default: Alembic, with the convention that every RLS policy is a versioned migration using `op.execute()` for `CREATE POLICY` / `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`. Alternatives (Atlas, Sqitch) add deps without payoff here.

14. **[VERIFIED] Ingestion scheduler framing service unnamed.** Doc 4 line 96 names `SELECT ... FOR UPDATE SKIP LOCKED` (the *claim* pattern) but not the *runner*. Options: APScheduler in-process (no multi-replica safety), ARQ/Celery (broker overhead), Azure Container Apps **jobs** with cron triggers (native, fits "configuration over code") — likely the right call.

15. **[VERIFIED] No SPA framework chosen.** Doc 4 line 153 rules out SSR. Candidates: React + Vite (boring, mature), SvelteKit static, Astro. "Easy to understand" priority points at React + Vite.

16. **[VERIFIED] IaC tool not selected.** User's brief listed Bicep vs Terraform. For Azure-only ACA, Bicep is lower-friction (native, no state-store problem).

17. **[VERIFIED] Secrets management unstated.** ACA + Key Vault references via managed identity is the canonical pattern; nothing in docs says so.

18. **[VERIFIED] Observability stack unspecified.** Doc 4 line 83's "per-poll telemetry" rows are demo-visibility, not operational observability. No mention of OpenTelemetry, Azure Monitor, or Application Insights. The "Visibility" priority is unmet on the ops side.

19. **[VERIFIED] Schema not specified.** Tables are named — `documents`, `document_versions`, `clauses`, `change_events`, `watchlists`, `saved_queries`, `dashboards`, `alert_preferences`, `subscriptions`, the implicit `document_poll_schedule`, `ingestion_incident`. No columns, PKs, FKs, indexes beyond doc 3 line 33's composite. Planning phase must produce DDL.

20. **[VERIFIED] How `app.user_id` GUC is set per request is unspecified and is RLS-load-bearing.** Doc 4 line 126 says it's set "on the connection before any query runs." With async pools, "the connection" is per-checkout. Correct mechanism: `SET LOCAL` inside an `async with` transaction bracketing the whole handler, dispatched by a FastAPI dependency / Litestar middleware. Without this, the GUC leaks across requests via recycled pool connections.

21. **[VERIFIED] PgBouncer interaction not addressed.** Azure DB for PG Flexible Server supports built-in PgBouncer in transaction-pooling mode. `SET LOCAL` is compatible *only inside an explicit transaction*; plain `SET` is session-scoped and silently leaks the previous request's `user_id`. Statement-pooling breaks `SET LOCAL` entirely. This is the single most common 2025/2026 RLS-tenancy production footgun and warrants its own ADR.

22. **[VERIFIED] Token storage on the SPA unspecified.** Doc 4 line 139: "stores the token client-side for the session." localStorage (XSS-exfiltratable) vs httpOnly secure samesite-strict cookie (CSRF risk, needs same-origin or token). For a B2B SPA on Azure, cookie is the standard answer.

23. **[VERIFIED] Admin-as-support audit log is open (doc 4 line 179) but implicit compliance requirement for a bank-facing product.** For the demo, a simple `admin_access_log(admin_user_id, target_user_id, endpoint, ts)` row written from the admin code path is sufficient.

24. **[VERIFIED] Repo layout not specified.** Three services share Postgres and presumably some code. Natural shape: monorepo with `services/ingestion`, `services/api`, `webapp/`, shared `horizons/` library.

25. **[VERIFIED] Per-document schedule table mentioned at doc 4 line 96 has no spec.** Columns, claim semantics, retry budget, backoff curve all undefined. This is the ingestion worker's heart.

26. **[VERIFIED] `effective_date` indexed (doc 3 line 33) but never defined.** Is it the date the change takes legal effect (the "horizon" framing)? Where does it come from in Lawstronaut? operational-notes.md mentions `publication_date` only. The product's headline ("upcoming legal changes — lead time before in force") rides on this column; its provenance is unstated.

---

## C. Implementation risks specific to this design

27. **[VERIFIED] Clause parser must handle two genuinely different substrates.** Doc 2 §"Irregular structure" + operational-notes.md lines 80–129. Per-portal recognition is a config-over-code surface, meaning each new portal needs a parser config row. With 30 jurisdictions × N portals, the parser-config table is the long-tail burden. Recommend a fallback "generic structural recogniser" + explicit overrides, not per-portal-only.

28. **[VERIFIED] 3-second p95 vs corpus-wide differential queries is plausibly violated at large result sets.** Differential responses include before/after content (doc 1 line 36). At p99 clause sizes inside 5 MB documents, 100 clauses paginated could push a single page past 3 s. Achievable, but requires deliberate index design and tight LIMITs — not free.

29. **[VERIFIED] RLS perf under subscription scoping with a security-definer function.** Postgres can't always inline security-definer functions into the query plan. Scope predicates must be index-friendly: jurisdiction and sector columns directly on `change_events`, evaluated via an in-line subscription-scope CTE or a generated columns approach, not via a function call per row. Needs EXPLAIN-tested validation before the schema is signed off.

30. **[VERIFIED] `SKIP LOCKED` doesn't save you from poison-pill documents.** Doc 4 line 96 names the lock pattern but not failure handling. The 3.8 MB AL outlier (and 20 MB outliers anticipated in doc 3 line 22) will time out or OOM. Need: per-document failure counter on the schedule row, exponential backoff, kill-switch above N failures, `ingestion_incident` wired in.

31. **[VERIFIED] Multi-tenant test scaffolding asserted (doc 4 line 129); pattern unspecified.** Two-client pytest with disjoint subscriptions requires: per-test transactional rollback that *also* resets `app.user_id`, fixture factory for users + subscriptions + watchlists, assertion helpers. With async SQLAlchemy + RLS + `SET LOCAL`, the pytest-asyncio + explicit transaction-bracket fixture is canonical. Worth a runbook before the first test.

32. **[VERIFIED] Self-rolled JWT pitfalls.** Key rotation, algorithm pinning (force `RS256`, reject `none` and `HS256` if asymmetric is configured), `kid` header handling, clock skew, JWKS endpoint compatible with Entra's later swap-in. The seam (an `AuthBackend` protocol with `verify_token() -> Principal`) needs to be drawn now.

---

## D. Sequencing

33. **[VERIFIED] Multi-tenant isolation must be de-risked first.** Minimum sequence: (1) Postgres schema with one private-state table and one corpus-shaped table stub, (2) RLS policies on both, (3) `app.user_id` GUC plumbing through async transaction brackets, (4) two-client pytest fixture asserting isolation. Anything built on top of this inherits the guarantee; anything built before has to be retrofitted.

34. **[VERIFIED] Parser + alignment pipeline parallelises off the critical-path.** It's a pure function over (markdown v1, markdown v2) → events. Stream of work: one contributor on parser + alignment + tests against the 31 fixtures; another on tenancy + RLS + repository; a third on ingestion-scheduler + Lawstronaut integration; webapp consumes finished endpoints last.

35. **[VERIFIED] Lawstronaut auth refresh is critical-path because full live polling is now a stated decision.** 30-min token TTL + 50 docs + undocumented Lawstronaut rate limits → harden the token-refresh seam first.

36. **[VERIFIED] SPA is end-of-chain but clause-diff render is technically risky.** Start the diff-render spike in parallel with alignment using static fixture diffs.

37. **[VERIFIED] Defer health/coverage endpoints and admin-as-support views.** Not on the critical path for the demo's headline moment.

---

## E. Honest assessment

38. **[VERIFIED] Strongest: the identity model (doc 2).** `clause_uid` vs `clause_path`, cheaper-then-fuzzier alignment, explicit monotonic-ordering constraint, honestly-named limitations (boilerplate collisions, large restructurings). Build it.

39. **[VERIFIED] Second-strongest: two-axis isolation principle.** Treating subscription leakage with the same severity as cross-tenant leakage is the right framing for B2B banks. Defence-in-depth is the textbook answer. Caveat: the *implementation* is where most production incidents will come from (findings 20, 21, 29).

40. **[VERIFIED] Weakest: the gap between "principle" and "endpoint."** Doc 4 names principles, services, one example URL prefix. No endpoint inventory. 20–40 endpoints' worth of detail will compound ambiguity in planning. Recommend an `endpoints.md` companion to doc 4 as the next doc.

41. **[VERIFIED] Most likely thing to bite us: `SET LOCAL app.user_id` + connection-pool + async-driver intersection.** A non-transactional code path (a quick read endpoint without `async with conn.transaction():`) silently runs against a recycled connection with the previous request's `app.user_id`. RLS evaluates against the wrong tenant. Intermittent in dev (single client), surfaces under concurrent load. Mitigation: a connection-acquisition helper that *always* opens a transaction and sets the GUC, with raw `acquire()` lint-banned — same posture as the existing raw-SQL ban.

42. **[VERIFIED] Over-engineered for the demo: persisting MinHash signatures by default (doc 2 line 132).** Cross-document near-duplicate detection is explicitly out-of-demo-scope (doc 4 line 172). Could default-off for the demo, default-on when a use case appears. Minor.

43. **[VERIFIED] Under-specified for production: schema details, observability, JWT key rotation, admin-support audit log, subscription-change semantics, and what happens when Lawstronaut returns an unanticipated shape violation beyond the known `T00:00:000Z` malformation.** None block the demo; each blocks productionisation.

44. **[VERIFIED] The doc chain is honest about its open questions — rare and valuable.** Every doc has an "Open questions" footer used as intended. Resist closing them performatively to look "more designed"; close them as decisions actually get made.

---

## Sources

- [Postgres Row-Level Security Footguns — Bytebase](https://www.bytebase.com/blog/postgres-row-level-security-footguns/)
- [PostgreSQL Row Level Security — Daniel Imfeld](https://imfeld.dev/notes/postgresql_row_level_security)
- [RLS sounds great until it isn't — PlanetScale](https://planetscale.com/blog/rls-sounds-great-until-it-isnt)
- [Row-Level Security with SQLAlchemy and Alembic](https://www.adrianovieira.eng.br/en/posts/architecture/row-level-security-sqlachemy-alembic-guide/)
- [DelfinaCare/rls — SQLAlchemy + Alembic RLS integration](https://github.com/DelfinaCare/rls)
- [Litestar vs FastAPI — Better Stack](https://betterstack.com/community/guides/scaling-python/litestar-vs-fastapi/)
- [PgBouncer Configuration and Best Practices — Heroku Dev Center](https://devcenter.heroku.com/articles/best-practices-pgbouncer-configuration)
