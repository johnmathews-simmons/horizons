# API sample data

Real responses captured from the Lawstronaut v2 API on 2026-06-04, kept here as a frozen reference for the clause-parser, diff engine, and any unit tests we write against parsed legal structure.

## Files

| File | Source | Notes |
|------|--------|-------|
| `ie-27732019-v1.md` | `GET /v2/contents/markdown?iso=IE&portal=www.irishstatutebook.ie&limit=1` (first record) | Protection of Employees (Employers' Insolvency) (Amendment) Act 2026 — 40.9 KB, dense clause structure with PART/section/(N)/(a)/(i) hierarchy. Good fixture for clause parsing. |
| `ie-27732019-v1.meta.json` | `GET /v2/contents?iso=IE&portal=www.irishstatutebook.ie&limit=1` (same record) | Metadata for the same document. Note: many date fields come back empty in the live response even though the doc shows otherwise. |

## Provenance details captured at fetch time

- `document_id`: `27732019` (returned as **string** from `/contents/markdown`, **number** from `/contents` — a documented inconsistency)
- `version`: `1`
- `portal`: `www.irishstatutebook.ie`
- `legal_link`: <https://www.irishstatutebook.ie/2026/en/act/pub/0007/index.html>
- Fetched: 2026-06-04
