# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Horizons** is a commercial demo for a legal firm. The productionised product is a regulatory-change intelligence service intended for large multinational banks: it watches public legal sources and alerts customers to **upcoming** legal changes — laws, regulations, and official guidance that have been published but have not yet taken effect — so clients have lead time to prepare before the change is in force. The "horizon" in the name refers to this forward-looking framing: changes visible on the horizon, not changes already landed. Demo is scheduled for ~2026-06-08; public for 1–2 days while on display, so **all copy and sample data must be generic** — no firm name, no client names, no real bank names.

The repo is **demo-ready** as of 2026-06-06. The design-doc chain in `docs/` is complete; a `uv` workspace with three Python members (`packages/horizons-{core,ingestion,api}`) plus a Vue 3 webapp at `packages/horizons-webapp` is in place. The FastAPI surface (auth + three primitives + admin + impersonation), the ingestion worker (long-running asyncio loop per ADR-0001), the SQLAlchemy + RLS database layer, the alignment pipeline, the Bicep IaC + `deploy.yml` blue/green pipeline, the OTel + structlog observability stack, and the testcontainers-backed integration suite have all landed. Dockerfiles for API + worker push to `ghcr.io/johnmathews/horizons-{api,worker}`. The Playwright e2e gates merges; the curated set + synthetic v2 fixtures are staged for the showcase. See `journal/260606-wu84-pre-demo-wrap.md` for the build summary. Follow the global rules in `~/.claude/CLAUDE.md` (Python 3.13, `uv`, `pytest`, type annotations, `/docs` per project, `/journal` per project).

The first end-to-end Azure deploy happened on 2026-06-06 after WU8.4. It surfaced a long ledger of Bicep ↔ application contract mismatches — all closed; the API + worker + SPA are live in `horizons-nonprod`. The session retrospective + post-demo punch list lives at `journal/260606-deploy-pipeline-end-to-end.md` — start there for the current state of the deploy pipeline, the closed bug list, and any IaC drift to clean up post-demo.

## Licensing

Closed-source. All rights reserved. The demo period (~2026-06-08, 1–2 days public for the legal-firm showcase) does **not** confer any license to use, modify, or redistribute the code — viewers can read but acquire no rights. A formal license decision is deferred until after the demo. There is intentionally no `LICENSE` file: default copyright applies, which is the most restrictive default.

## Read first

Before doing anything substantive, read in this order:

1. `docs/0. about-these-docs.md` — meta-doc framing the design-doc chain as a linked RFC chain (with ADRs as a complementary practice) and explaining why the chain exists in this form.
2. The numbered design-doc chain — these build on each other:
   - `docs/1. product-questions.md` — the three primitives the tool must answer (discovery, temporal, differential) and the scope/filter/delivery dimensions.
   - `docs/2. clause-alignment.md` — how clauses keep identity across versions; the alignment pipeline and similarity stack.
   - `docs/3. database-design.md` — performance target, scale assumptions, principles; depends on the identity model from doc 2.
   - `docs/4. services.md` — the three deployable services (ingestion worker, public API, SPA webapp), their responsibilities and non-responsibilities, and the cross-cutting principles (multi-tenant isolation, API responsiveness, single API surface).
   - `docs/5. clause-tree-parser.md` — markdown → `Clause` tree transform that produces the substrate doc 2's alignment pipeline consumes. Numbered 5 because added later, but conceptually a prerequisite to doc 2; read after the main chain.
3. `docs/api/README.md` — index of two API surfaces. **Horizons (what we ship):** `endpoints.md` (auto-generated from FastAPI OpenAPI by `packages/horizons-api/scripts/regen_endpoints_md.py`; do not hand-edit), `horizons-primitives.md`, `auth.md`. **Lawstronaut (what we consume):** `getting-started.md`, `concepts.md`, `lawstronaut-endpoints.md`, `operational-notes.md`.
4. `docs/plan/improvement-plan.md` — the work-unit roadmap (tracks 0–8, WU numbers referenced throughout `journal/`) produced from the 2026-06-04 engineering-team evaluation. `docs/plan/evaluation-report.md` is the baseline assessment it was built from; `docs/plan/discussions/` carries the per-dimension subagent reports.
5. `data/samples/README.md` — what the sample legal markdown is, how it was collected (`scripts/fetch_fixtures.py`), and the current 31-fixture inventory.
6. The memory entries for this project. These live in-repo at `.claude/memory/` (gitignored — synced across machines via syncthing, not git) and are also auto-loaded by the harness via a symlink at `~/.claude/projects/-Users-john-projects-syncthing-agent-lxc-horizons/memory` → `.claude/memory/`. The index is `.claude/memory/MEMORY.md`; write new entries here and they'll be picked up next session.
   - `project-horizons-business-context` — what we're selling and to whom.
   - `project-horizons-change-watcher` — clause-level scope decision.
   - `project-horizons-multi-tenant-isolation` — two-axis isolation; defence-in-depth enforcement.
   - `project-horizons-deployment-constraints` — Azure Container Apps default.
   - `project-horizons-design-priorities` — flexibility > visibility > easy-to-understand.
   - `project-horizons-demo-2026-06-08` — audience, public exposure, tiered docs.
   - `lawstronaut-api-key-facts` — non-obvious API gotchas (verified against live API on 2026-06-04).
   - `feedback-doc-style` — tight anchor-style for project docs; no chatty intermediate forms.

## Architectural decisions already made

These are load-bearing — don't relitigate them without checking with John first.

- **Change detection is at the clause level, not the document level.** Legal docs follow Part / Section / sub-section / (a) / (i) conventions. A new `version` of an Act typically only touches a few clauses; the demo's headline moment is showing *which clause changed and how*.
- **`/v2/contents/markdown` is the preferred content feed.** Markdown preserves the structural anchors we use as clause boundaries. Plain `/contents/full-text` and PDFs are weaker substrates.
- **Clause IDs must be heading-anchored, not positional.** A clause keeps its identity across versions even when neighbours move or get renumbered.
- **Deployment target is Azure Container Apps / Container Instances.** Not AKS/Kubernetes (overkill at demo scale), not Databricks (the v1 of this project is on Databricks and we're moving away from it).
- **Configuration over code for sources / jurisdictions / domains *and* experimental tuning parameters.** Adding a new portal must not require a redeploy or a refactor — it should be a config/data change. The same applies to anything we expect to tune during the demo: shingling *k*, MinHash signature size, similarity / confidence thresholds, etc. live as runtime-tunable config (surfaced in the UI), not as code constants. Slow redeploy cycles for parameter experimentation are unacceptable, at least during the demo period.
- **Multi-tenant isolation is two-axis and load-bearing from day one.** (1) Cross-client privacy: no client can observe any state belonging to another client (watchlists, alerts, saved queries, dashboards, subscriptions). (2) Subscription scoping of corpus access: each client buys a subscription (set of jurisdictions × sectors) and cannot query corpus rows outside it — a UK-only client cannot see EU change events. Treated with the same severity as cross-tenant leakage.
- **Three deployable services**: an ingestion worker (scheduled, hits Lawstronaut), a public REST API (HTTP, the only surface anyone talks to), and an SPA webapp (a customer of the same public API as external customers — no internal back-channel). API responsiveness must not be affected by ingestion bursts; the two share Postgres but run in separate containers with disjoint hot paths.
- **Defence-in-depth for isolation, not predicate-only.** Private-state tables protected by Postgres Row-Level Security + a repository / query-helper layer + lint-banned raw SQL + multi-user integration tests. Corpus tables under client-role reads filtered by subscription scope at the repository layer with an RLS scope policy as the second layer. `admin` role bypasses subscription gating via an audited code path.
- **Two roles: `admin` (us, the operator) and `client` (paying customers).** Admin sees system health, manages subscriptions, and can view any client's private state for support. Client-to-client visibility is zero on both isolation axes regardless of role.

## Lawstronaut API quick reference

- **API base:** `https://api.lawstronaut.com/v2` — bearer token in `Authorization: Bearer <token>`.
- **Auth base (different host):** `POST https://filerskeepersapi.co/auth/login` returns a token field named `refresh_token` that is *itself* the bearer used on API requests; `expires_in` is seconds (30 min).
- **For one-off testing during development:** the dev portal home page at `https://dev-portal.filerskeepersapi.co/dashboard/lawstronaut/home` displays a current JWT (30-min TTL) that can be copied for ad-hoc calls. Real code should use the documented login + refresh flow.
- **Docs vs. reality discrepancies** (confirmed against the live API and captured in `docs/api/operational-notes.md`): markdown field is `content_markdown` (not `markdown`); `document_id` is sometimes string sometimes number — treat as string; `/v2/contents/markdown?document_id=X` returns 400; `/v2/content/{id}` without version returns 403; `/v2/content/{id}/{version}` returned 200 with empty `data` for the IDs we tried (open question); `publication_date` values come back with malformed milliseconds like `T00:00:000Z` — parsers must tolerate.

## Useful sample data

`data/samples/` holds 31 real legal documents in markdown from the Lawstronaut API — the original Irish Statute Book Act plus 30 round-robin captures across 30 jurisdictions and languages, collected 2026-06-04. Sizes range from 721 B (HR) to 3.8 MB (AL). Each document has a `<iso>-<docid>-v<n>.md` content file and a `.meta.json` sidecar with a `_provenance` block. `data/samples/fixtures.json` is the machine-readable inventory. The IE Act (`ie-27732019-v1.md`) has dense `PART N` / `**N\.**` / `(N\)` / `(a)` / `(i)` clause structure; the CZ document (`cz-29662776-v1.md`) is at the other extreme — clause structure expressed inline via `ČÁST PRVNÍ` / `Čl. I` / `N\.` with no markdown headings. The parser will need to handle both substrates.

## Commands

The repo is a `uv` workspace (root `pyproject.toml` with `[tool.uv.workspace]`) plus a separate npm-managed webapp under `packages/horizons-webapp`. The Python members live under `packages/horizons-{core,ingestion,api}`; the webapp is not a `uv` workspace member.

**To boot the stack on your laptop** (Postgres + API + webapp), follow `docs/runbooks/local-dev.md`. The worker is documented at the bottom of that runbook but is not part of the default local flow — it needs Azure Blob + Lawstronaut credentials.

**First-time setup after cloning:**

```bash
uv sync                          # install Python workspace + dev tools
uv run pre-commit install        # enable git pre-commit hooks
cd packages/horizons-webapp && npm install && cd -
```

Verify the hook was actually installed with `ls .git/hooks/pre-commit`. If the install reported `Cowardly refusing to install hooks with core.hooksPath set`, the repo has an orphan `git config core.hooksPath` pointing at the default location — unset it (`git config --unset core.hooksPath`) and rerun `uv run pre-commit install`.

**Day-to-day:**

- `uv run pytest` — full Python test suite (pytest uses `--import-mode=importlib` so per-package `test_smoke.py` filenames don't collide; `asyncio_mode=auto` so `async def test_*` functions don't need a decorator). Integration tests marked `integration` spin up a testcontainers Postgres 18 and auto-skip if Docker isn't reachable. Slow Hypothesis property tests marked `nightly` are excluded by default via `-m 'not nightly'` in `addopts`.
- `uv run pytest -m "not integration"` — fast unit tests only (no Docker required).
- `uv run pytest -m integration` — Docker-backed integration tests only.
- `uv run pytest -m nightly` — slow Hypothesis property tests (also Docker-backed). Run by `.github/workflows/nightly.yml` on a 04:00 UTC schedule + `workflow_dispatch`; non-gating.
- `uv run pytest --cov` — full suite with coverage; HTML report via `uv run pytest --cov --cov-report=html` → `htmlcov/`.
- `uv run pytest packages/horizons-core/tests/test_smoke.py::test_package_imports` — single test by path.
- `uv run ruff check .` — lint Python.
- `uv run ruff format .` — format Python.
- `uv run pyright` — strict typecheck the three Python members.
- `uv run pre-commit run --all-files` — run all pre-commit hooks across the whole tree (also fires automatically on `git commit`).
- `cd packages/horizons-webapp && npm run dev` — Vite dev server.
- `cd packages/horizons-webapp && npm run build` — production build (vue-tsc + Vite).
- `cd packages/horizons-webapp && npm run test:unit` — Vitest.
- `cd packages/horizons-webapp && npm run lint && npm run format` — oxlint + eslint + prettier with `--fix` (local dev only; CI uses the no-fix `npm run lint:check`).
- `cd packages/horizons-webapp && npm run lint:check` — same linters with no `--fix`; what CI runs. Will fail on any unfixable diagnostic.
- `uv run alembic upgrade head` — apply Postgres migrations. Reads connection URL from `HORIZONS_DB_URL`. Migration tree lives at `packages/horizons-core/migrations/`. Role model docs: `packages/horizons-core/src/horizons_core/db/roles.md`.
- `uv run scripts/fetch_fixtures.py` — re-runnable Lawstronaut fixture fetcher; skips slugs already on disk. Reads creds from `.env` (see `.env.example`).
- `uv run python scripts/seed_curated_set.py` — seed `documents` + `document_poll_schedule` from `data/curated_set.yaml`; idempotent. Pass `--stage-synthetic-v2` to additionally stage the five hand-authored v2 documents under `data/samples/synthetic_v2/` (parks the corresponding `next_poll_at` at `2026-12-31` so the worker can't claim staged docs — see `journal/260605-fix-worker-staged-guard-and-env-validation.md`). `--dry-run` parses and aligns without writing.
- `uv run python packages/horizons-api/scripts/create_demo_accounts.py` — provision the three demo accounts (`demo-uk@example.test`, `demo-eu@example.test`, `admin-demo@example.test`). Default run is create-or-rotate with a no-downgrade guard (refuses to overwrite a real credential with the bake-in default); `--reset` deletes and recreates. Override passwords via `HORIZONS_DEMO_{UK,EU,ADMIN}_PASSWORD`. Documented in `docs/runbooks/demo-accounts.md`.
- `uv run python packages/horizons-api/scripts/seed_e2e.py` — separate e2e fixture seed used by `.github/workflows/e2e.yml`; `@e2e.test` TLD, distinct from the demo accounts above. `--teardown` purges.
- `uv run python packages/horizons-api/scripts/regen_endpoints_md.py` — regenerate `docs/api/endpoints.md` from the FastAPI OpenAPI. Pre-commit runs `--check`; never hand-edit `endpoints.md`.

**CI / deployment workflows (manual triggers):**

- `gh workflow run deploy.yml --field environment=staging` — manual staging deploy. Normally fires automatically on push to `main` via `workflow_run` after `build-and-push.yml` succeeds. See `docs/runbooks/deploy.md` for the blue/green revision flip + SPA upload + Front Door purge sequence and the prerequisites table (storage `$web` static-website flag, ACA env bound to Log Analytics, `POSTGRES_ADMIN_PASSWORD` set on the `staging` environment, `Storage Blob Data Contributor` granted to the UAMI).
- `gh workflow run e2e.yml` — manual run of the Playwright e2e (`packages/horizons-webapp/e2e/login-and-scope.spec.ts`). Normally fires on every push to `main`. Failures upload Playwright traces as artefacts.
- `gh workflow run drift-check.yml` — manual `az deployment group what-if` against `horizons-nonprod`; opens a GH issue labelled `infra-drift` if the change set is non-empty. Also scheduled at 03:00 UTC nightly. Note: currently also fires on push (cosmetic noise; post-demo cleanup, see `journal/260606-wu84-pre-demo-wrap.md`).
- `cd packages/horizons-webapp && npx playwright test` — run the e2e locally against a manually-booted stack. The boot recipe (Postgres in Docker on port 5433, alembic + `seed_e2e.py` with sync `+psycopg` URL, uvicorn with async `+asyncpg` URL, `npm run build` + `npx vite preview`) is in `packages/horizons-webapp/e2e/README.md`; the five-bug story behind why each step matters is in `journal/260605-wu82-hotfix-e2e-cors.md`.

**Azure operations (occasional):**

- `az provider show --namespace <ns> --query registrationState -o tsv` — verify a provider registration before the first Bicep deploy. The first ever `az deployment group create` against `horizons-nonprod` needs `Microsoft.App`, `Microsoft.Cdn`, `Microsoft.OperationalInsights`, `Microsoft.Insights`, `Microsoft.DBforPostgreSQL`, `Microsoft.KeyVault`, and `Microsoft.Storage` all in `Registered`. Register a missing one with `az provider register --namespace <ns>` and poll until `Registered`.
- `az containerapp env update --logs-destination log-analytics --logs-workspace-id <id>` — one-off, post-Bicep-deploy, to wire the ACA managed OTEL agent to the App Insights-backed Log Analytics workspace. Without it the API + worker stdout logs land in ACA's own log stream but not in App Insights, so the `WU7.3` alert rules (when enabled) sit in "Insufficient data" forever.

## CI / merge cadence

The merge cadence is **worktree → fast-forward main → direct push**. Branch protection on `main` (ruleset `protect-main`, ID 17308903) keeps linear history, blocks force pushes, and blocks deletions, but does **not** require remote status checks — the local sweep (`uv run pytest`, `ruff check`, `pyright`, `pre-commit run --all-files`, and the webapp's `npm run lint:check && npm run build && npm run test:unit -- --run`) is the actual gate before pushing main. Remote CI runs as verification, not as a precondition.

Step-by-step from a finished worktree on branch `<feature>`:

```bash
# 1. From the worktree, after the local sweep is green:
git push -u origin <feature>      # triggers CI on the feature branch (early signal)

# 2. From the main checkout (or via -C):
git -C /Users/john/projects/syncthing/agent-lxc/horizons merge --ff-only <feature>
git -C /Users/john/projects/syncthing/agent-lxc/horizons push origin main
git push origin --delete <feature>  # remove the now-merged remote branch
# Then ExitWorktree(action="remove") to drop the local worktree + branch.
```

Workflows (`.github/workflows/ci.yml`, `webapp.yml`) trigger on `pull_request`, `push` (any branch — no filter), and `workflow_dispatch`. Feature-branch pushes therefore get the same CI runs as PRs and pushes to main; status checks accumulate against the commit SHA but are **not** gating because the ruleset no longer requires them.

PRs remain available — `gh pr create` works the same way — but the routine is direct merge, not PR.

History (for posterity): an earlier version of `protect-main` required two status checks (`lint, typecheck, test` and `lint, build, test`) before any push to main. That rule made direct push impossible because GitHub keys required checks to a check_suite's `head_branch`, so checks run on a feature branch (or via `workflow_dispatch`) don't count for main even when they're green on the same SHA. The rule was dropped on 2026-06-05; local CI is the gate now.

## Journal cadence

Global rule (`~/.claude/CLAUDE.md`): each project has a `/journal/` with dated entries (`yymmdd-descriptive-name.md`). Start an entry per session of substantive work — what was decided, what was learned, what's next. The first entry is `journal/260604-initial-design-and-fixtures.md`; read it before resuming work so you know which decisions are load-bearing and what next-session priorities the previous session left behind.
