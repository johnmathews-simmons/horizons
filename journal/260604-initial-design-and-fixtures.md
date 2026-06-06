# 2026-06-04 — Initial design and fixtures

First substantive session on Horizons. Started from zero (only `docs/api/` existed), ended with a public GitHub repo, three numbered design docs, 30 multi-jurisdiction fixtures, and a re-runnable fetch script.

## Done

- **Repo:** `github.com/johnmathews/horizons`, public. README leads with the lead-time framing (alerting customers to *upcoming* legal changes before they take effect — not past-tense amendment notification). The "horizon" name encodes this.
- **Design-doc chain** (read in order):
  - `docs/RFC-1 product-questions.md` — three primitives: **discovery** (cheap "what changed in this corpus?"), **temporal** ("when?"), **differential** ("what changed, with before/after?"). Scope nests corpus → document → clause. Filter (jurisdiction, sector, time) defines a corpus scope. Discovery and differential compose into the typical customer flow: poll cheap, then ask for full content.
  - `docs/RFC-2 clause-alignment.md` — `clause_uid` (stable identity, carried across versions by alignment) separate from `clause_path` (positional label, renumbers freely). Alignment pipeline at ingestion: source-provided IDs → heading-title-plus-content match → monotonic content-similarity (k-shingles + MinHash + LSH, Needleman–Wunsch DP). Change types `{ADDED, REMOVED, MODIFIED, MOVED}` with `alignment_confidence ∈ [0, 1]` per row. Tuning parameters (shingling *k*, MinHash size, thresholds) are runtime-configurable via UI, not code constants.
  - `docs/RFC-3 database-design.md` — Postgres + Azure Blob; append-only history; `change_events` as the load-bearing table; **3-second p95 query target**; all original markdown goes to blob storage regardless of size (no inline-vs-blob threshold).
- **Fixtures:** 30 documents across 30 jurisdictions (AD AE AL AT AU BE BR CH CN CY CZ DE DK ES EU FI FJ FR GB GE GR HR HU IE IT JP KR LU LV MC). Languages span Catalan, Arabic, English, German, French, Czech, Spanish, Greek, Croatian, Hungarian, Italian, Japanese, Korean, Latvian, Chinese, … Size spread 721 B → 3.8 MB. Inventory: `data/samples/fixtures.json`.
- **Fetch script:** `scripts/fetch_fixtures.py` (uv PEP-723 inline script). Re-runnable; skips slugs already present.
- **API operational findings** in `docs/api/operational-notes.md`:
  - Login response nests bearer at `payload["data"]["token"]["refresh_token"]`.
  - `/v2/portals` requires `iso`; `limit`/`offset` rejected.
  - `language=English` filter returns HTTP 400 despite the docs.
  - `legal_link` URLs may embed source-portal session tokens that rot (e.g. AT `ResultFunctionToken`).
  - Several non-English documents express clause structure inline (CZ: `ČÁST PRVNÍ` / `Čl. I` / `N\.`) instead of via markdown headings — the parser must handle both substrates.

## Key decisions (don't relitigate without checking)

| Decision | Where |
|---|---|
| Diff at clause level, not document | pre-existing, in CLAUDE.md |
| Markdown is the preferred content substrate | pre-existing |
| `clause_uid` (stable) separate from `clause_path` (positional) | doc 2, principle 4 of doc 3 |
| `change_events` are first-class precomputed records | doc 3, principle 3 |
| All original markdown goes to blob storage; no threshold | doc 3, principle 5 |
| Experimental tuning parameters live as runtime UI config, not code constants | CLAUDE.md "Architectural decisions"; doc 2 tuning table |
| Alignment confidence is a raw float in [0, 1]; no bucketed labels | doc 2 |
| Lawstronaut has no per-clause IDs today; ELI-style URIs are a future lever | doc 2 |

## Next session — priorities

1. **Clause parser.** Two patterns to handle first: IE markdown-heading + `**N\.**` style, and CZ inline-label style (`ČÁST PRVNÍ` / `Čl. I` / `N\.`). Per-portal recognition strategy.
2. **Alignment pipeline.** Shingling + MinHash + LSH + Needleman–Wunsch over clauses.
3. **Postgres schema.** `documents` / `document_versions` / `clauses` / `change_events`. Wire up Azure Blob for originals.
4. **Resolve open Lawstronaut questions.** `/v2/content/{id}/{version}` returns empty `data` for the IDs we tried — figure out why before relying on it for change detection.
5. **Source-link rot.** Strategy for canonicalising / revalidating `legal_link` URLs that embed source-portal session tokens.
6. **Project plumbing.** Python package layout, `pyproject.toml`, tests (`pytest`), Dockerfile + GHCR workflow per global CLAUDE.md, journal cadence.
