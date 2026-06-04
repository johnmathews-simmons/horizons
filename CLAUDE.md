# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Horizons** is a commercial demo for a legal firm. The productionised product is a regulatory-change intelligence service intended for large multinational banks: it watches public legal sources and alerts customers to **upcoming** legal changes — laws, regulations, and official guidance that have been published but have not yet taken effect — so clients have lead time to prepare before the change is in force. The "horizon" in the name refers to this forward-looking framing: changes visible on the horizon, not changes already landed. Demo is scheduled for ~2026-06-08; public for 1–2 days while on display, so **all copy and sample data must be generic** — no firm name, no client names, no real bank names.

The repo is in **early scaffolding**. The design-doc chain in `docs/` is complete; a `uv` workspace with three Python members (`packages/horizons-{core,ingestion,api}`) plus a Vue 3 webapp at `packages/horizons-webapp` is in place; `tests/` holds cross-package integration tests; ruff + pyright + pre-commit are wired at the workspace root; GitHub Actions CI runs the Python sweep and the webapp build on every PR. Application code, the database layer, the FastAPI surface, the testcontainers-backed integration suite, and Dockerfiles are still to come. Follow the global rules in `~/.claude/CLAUDE.md` (Python 3.13, `uv`, `pytest`, type annotations, `/docs` per project, `/journal` per project).

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
3. `docs/api/README.md` — entry point to the Lawstronaut v2 API reference. Then `getting-started.md`, `concepts.md`, `endpoints.md`, `operational-notes.md`.
4. `data/samples/README.md` — what the sample legal markdown is, how it was collected (`scripts/fetch_fixtures.py`), and the current 31-fixture inventory.
5. The memory entries for this project. These live in-repo at `.claude/memory/` (gitignored — synced across machines via syncthing, not git) and are also auto-loaded by the harness via a symlink at `~/.claude/projects/-Users-john-projects-syncthing-agent-lxc-horizons/memory` → `.claude/memory/`. The index is `.claude/memory/MEMORY.md`; write new entries here and they'll be picked up next session.
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

**First-time setup after cloning:**

```bash
uv sync                          # install Python workspace + dev tools
uv run pre-commit install        # enable git pre-commit hooks
cd packages/horizons-webapp && npm install && cd -
```

**Day-to-day:**

- `uv run pytest` — full Python test suite (pytest uses `--import-mode=importlib` so per-package `test_smoke.py` filenames don't collide; `asyncio_mode=auto` so `async def test_*` functions don't need a decorator). Integration tests marked `integration` spin up a testcontainers Postgres 17 and auto-skip if Docker isn't reachable.
- `uv run pytest -m "not integration"` — fast unit tests only (no Docker required).
- `uv run pytest -m integration` — Docker-backed integration tests only.
- `uv run pytest --cov` — full suite with coverage; HTML report via `uv run pytest --cov --cov-report=html` → `htmlcov/`.
- `uv run pytest packages/horizons-core/tests/test_smoke.py::test_package_imports` — single test by path.
- `uv run ruff check .` — lint Python.
- `uv run ruff format .` — format Python.
- `uv run pyright` — strict typecheck the three Python members.
- `uv run pre-commit run --all-files` — run all pre-commit hooks across the whole tree (also fires automatically on `git commit`).
- `cd packages/horizons-webapp && npm run dev` — Vite dev server.
- `cd packages/horizons-webapp && npm run build` — production build (vue-tsc + Vite).
- `cd packages/horizons-webapp && npm run test:unit` — Vitest.
- `cd packages/horizons-webapp && npm run lint && npm run format` — oxlint + eslint + prettier (not in pre-commit; CI in WU0.4 will enforce).
- `uv run scripts/fetch_fixtures.py` — re-runnable Lawstronaut fixture fetcher; skips slugs already on disk. Reads creds from `.env` (see `.env.example`).

Docker image / GHCR workflow land in later work units (per the global rule in `~/.claude/CLAUDE.md`).

## Journal cadence

Global rule (`~/.claude/CLAUDE.md`): each project has a `/journal/` with dated entries (`yymmdd-descriptive-name.md`). Start an entry per session of substantive work — what was decided, what was learned, what's next. The first entry is `journal/260604-initial-design-and-fixtures.md`; read it before resuming work so you know which decisions are load-bearing and what next-session priorities the previous session left behind.
