# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Horizons** is a commercial demo for a legal firm. The productionised product is a regulatory-change intelligence service intended for large multinational banks: it watches public legal sources and alerts customers to **upcoming** legal changes — laws, regulations, and official guidance that have been published but have not yet taken effect — so clients have lead time to prepare before the change is in force. The "horizon" in the name refers to this forward-looking framing: changes visible on the horizon, not changes already landed. Demo is scheduled for ~2026-06-08; public for 1–2 days while on display, so **all copy and sample data must be generic** — no firm name, no client names, no real bank names.

The repo is currently at the **pre-code, documentation-gathering** stage. Only `docs/` and `data/samples/` exist. There is no Python package, no tests, no Dockerfile, no CI yet — those are still to be designed. When you add them, follow the global rules in `~/.claude/CLAUDE.md` (Python 3.13, `uv`, `pytest`, type annotations, `/docs` per project, `/journal` per project).

## Read first

Before doing anything substantive, read in this order:

1. `docs/api/README.md` — entry point to the Lawstronaut v2 API reference. Then `getting-started.md`, `concepts.md`, `endpoints.md`, `operational-notes.md`.
2. `data/samples/README.md` — what the sample legal markdown is and where it came from.
3. The memory entries for this project (auto-loaded for you by the harness):
   - `project-horizons-business-context` — what we're selling and to whom.
   - `project-horizons-change-watcher` — clause-level scope decision.
   - `project-horizons-deployment-constraints` — Azure Container Apps default.
   - `project-horizons-design-priorities` — flexibility > visibility > easy-to-understand.
   - `project-horizons-demo-2026-06-08` — audience, public exposure, tiered docs.
   - `lawstronaut-api-key-facts` — non-obvious API gotchas (verified against live API on 2026-06-04).

## Architectural decisions already made

These are load-bearing — don't relitigate them without checking with John first.

- **Change detection is at the clause level, not the document level.** Legal docs follow Part / Section / sub-section / (a) / (i) conventions. A new `version` of an Act typically only touches a few clauses; the demo's headline moment is showing *which clause changed and how*.
- **`/v2/contents/markdown` is the preferred content feed.** Markdown preserves the structural anchors we use as clause boundaries. Plain `/contents/full-text` and PDFs are weaker substrates.
- **Clause IDs must be heading-anchored, not positional.** A clause keeps its identity across versions even when neighbours move or get renumbered.
- **Deployment target is Azure Container Apps / Container Instances.** Not AKS/Kubernetes (overkill at demo scale), not Databricks (the v1 of this project is on Databricks and we're moving away from it).
- **Configuration over code for sources / jurisdictions / domains.** Adding a new portal must not require a redeploy or a refactor — it should be a config/data change.

## Lawstronaut API quick reference

- **API base:** `https://api.lawstronaut.com/v2` — bearer token in `Authorization: Bearer <token>`.
- **Auth base (different host):** `POST https://filerskeepersapi.co/auth/login` returns a token field named `refresh_token` that is *itself* the bearer used on API requests; `expires_in` is seconds (30 min).
- **For one-off testing during development:** the dev portal home page at `https://dev-portal.filerskeepersapi.co/dashboard/lawstronaut/home` displays a current JWT (30-min TTL) that can be copied for ad-hoc calls. Real code should use the documented login + refresh flow.
- **Docs vs. reality discrepancies** (confirmed against the live API and captured in `docs/api/operational-notes.md`): markdown field is `content_markdown` (not `markdown`); `document_id` is sometimes string sometimes number — treat as string; `/v2/contents/markdown?document_id=X` returns 400; `/v2/content/{id}` without version returns 403; `/v2/content/{id}/{version}` returned 200 with empty `data` for the IDs we tried (open question); `publication_date` values come back with malformed milliseconds like `T00:00:000Z` — parsers must tolerate.

## Useful sample data

`data/samples/ie-27732019-v1.md` is a real Irish Statute Book Act (~41 KB of markdown). It has dense `PART N` / `**N\.**` / `(N\)` / `(a)` / `(i)` clause structure and is a good fixture for the clause parser and diff engine when those exist.

## Commands

There are no project-specific build / lint / test commands yet — the codebase has no code. When that changes, add:
- How to install deps (`uv sync`).
- How to run the watcher locally.
- How to run the full test suite and a single test (`pytest path/to/test.py::test_name`).
- How to build the container image and push to ACR.

Update this section the moment those commands exist.
