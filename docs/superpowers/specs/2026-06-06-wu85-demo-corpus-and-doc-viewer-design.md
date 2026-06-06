# WU8.5 — Demo corpus expansion + document viewer

**Status:** approved 2026-06-06.
**Depends on:** WU8.1 demo accounts, WU3.x clause parser + persistence, the existing `DocumentsRepository` / `DocumentVersionsRepository` / `ClausesRepository`.

## Why

The post-WU8.4 demo database only seeds the ~10 documents in `data/curated_set.yaml`. The UK and EU demo accounts each see one document (the synthetic-v2 headline diff). For a public showcase the viewer needs depth: more documents per account, a way to open any of them, and a visible answer to "what's the parser's unit of work?"

The atomic unit is the **clause**. The viewer must make that legible — not just by rendering the document, but by letting the user toggle the parser's interpretation overlay on.

## Scope

1. Seed every fixture in `data/samples/*.md` (31 documents) into the demo database.
2. Relabel ~10 fixtures to `jurisdiction: UK` and ~10 to `jurisdiction: EU` via `curated_set.yaml` overrides so each demo subscription resolves ≥10 documents.
3. Add `/v1/documents` + `/v1/documents/{id}` + `/v1/documents/{id}/versions/{version}` + `.../clauses` endpoints, scope-checked through the existing RLS path; admin sees all.
4. Add **Documents** to the client and admin navs. New Vue views: list (search + filter) and detail (markdown render + structure-overlay toggle).
5. Tests across all three layers: pytest for the new router, vitest for `ClauseOverlay`, a Playwright spec for the toggle.

## Out of scope

- No new fixture fetching — every needed file is on disk.
- No clause-diff highlighting in the viewer (lives in the Changes view).
- No editing surface; read-only.
- No ACA re-seed automation. The operator runs `scripts/reseed_aca.sh --yes` separately when staging should pick up the bigger corpus.

## Data layer

No migrations, no model changes. The parser already persists `clauses.text_content`, `clauses.clause_path`, `clauses.ord` — that triple is enough to render both views without going to blob storage.

`curated_set.yaml` schema is unchanged. Add ~21 entries. Tentative split (real jurisdiction → demo relabel):

- **UK (10):** IE-8064194, GB-28914588 (already), AU-2145602, NZ-* (if present), GE-4446542, CY-31683899, MT-* (if present), IE-* others, plus any English-language fixture suitable for the UK-banking framing.
- **EU (10):** FR-31702142 (already), DE-20951816, BE-19194112, AT-32061749, IT-26863, ES-28885109, NL-* (if present), DK-18087738, FI-28628500, GR-3539403, CZ-29662776, HU-9119685.

Final list resolved when implementing — must total ≥10 UK and ≥10 EU after deduping against the inventory in `data/samples/fixtures.json`. Other fixtures stay on their original jurisdiction (admin sees them; clients don't).

Sectors stay at `sectors[0]` = `BANKING` for unannotated docs, matching the WU8.1 demo framing.

## API surface

New file `packages/horizons-api/src/horizons_api/routes/documents.py`. One router, four GETs, all under `/v1/documents`. All return `Cache-Control: private, no-store` like the primitives. Auth dep: `authenticated_user` + `session_for_request` for the scope-checked path; admin endpoints reuse `admin_operator_session_for_request` so the same handler serves both roles when the caller is admin — see "Admin bypass" below.

### `GET /v1/documents`

Query: `jurisdiction?`, `sector?`, `search?` (substring on title, ILIKE), `limit` (default 50, max 200), `cursor?` (opaque, base64 over `(created_at, id)`).

Response:

```json
{
  "items": [
    {
      "id": "uuid",
      "jurisdiction": "UK",
      "sector": "BANKING",
      "title": "...",
      "lawstronaut_document_id": "8064194",
      "latest_version_label": "v1",
      "version_count": 1,
      "created_at": "..."
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

Latest-version fields come from a one-shot LEFT-JOIN aggregation against `document_versions` in `DocumentsRepository.list_with_version_summary()` (new method). RLS handles scope filtering for clients; admin runs under `api_operator` so the same query returns everything.

### `GET /v1/documents/{document_id}`

Returns the same item shape plus an array of versions:

```json
{
  "id": "uuid",
  "jurisdiction": "...",
  "sector": "...",
  "title": "...",
  "lawstronaut_document_id": "...",
  "created_at": "...",
  "versions": [
    { "id": "uuid", "version_label": "v1", "effective_date": "...", "publication_date": "...", "content_bytes": 12345 }
  ]
}
```

404 if the document is not in scope (RLS returns no row) — mirror the primitives' behavior; the client can't distinguish "not found" from "not in scope," which is intentional for privacy.

### `GET /v1/documents/{document_id}/versions/{version_label}`

Returns the version-detail metadata (no body). 404 if document or version not in scope.

### `GET /v1/documents/{document_id}/versions/{version_label}/clauses`

Returns the flat, ordered list of clauses:

```json
{
  "document_id": "...",
  "version_id": "...",
  "version_label": "v1",
  "clauses": [
    {
      "id": "uuid",
      "clause_uid": "uuid",
      "clause_path": "PART_1/SECTION_2/(a)",
      "text_content": "..."
    }
  ]
}
```

Sorted by `clauses.ord`. The webapp uses `ord` for reading order and `clause_path` for the overlay's anchor chip. No depth field on the wire — the webapp derives depth from the path's `/`-segment count.

### Admin bypass

Admin doesn't need a parallel `/admin/documents` tree. The router declares a single dependency that resolves to whichever session the caller's principal warrants: `api_app` for clients (RLS-scoped), `api_operator` for admin (full visibility, audit-logged). New helper `session_for_request_or_admin` in `horizons_api/deps/session.py`, modeled on `admin_operator_session_for_request` but role-aware. The audit log entry uses the existing `admin_audit.audit_admin_access(...)` call site.

## Webapp

New files under `packages/horizons-webapp/src/`:

- `views/DocumentsListView.vue` — paginated table. Columns: title (links to detail), jurisdiction, sector, version count, created at. Above the table: `<input>` for title search, `<select>` for jurisdiction, `<select>` for sector. Debounced search, query-param-synced filters so the URL is shareable.
- `views/DocumentDetailView.vue` — top: title, jurisdiction/sector chips, version dropdown (defaults to latest). Right-aligned toggle button: `Show clause structure`. Body: a single component that takes the clause list and a mode prop.
- `components/documents/DocumentBody.vue` — receives `clauses[]` and `showStructure: boolean`. When off, renders clauses' `text_content` concatenated with double newlines and passed through `marked` → sanitized → injected. When on, renders each clause as a card with the anchor chip on top.
- `components/documents/ClauseOverlay.vue` — internal child for the structure mode: anchor chip (e.g. `PART_2/SECTION_4/(a)/(i)`), depth-derived indent, card body rendering the same clause text through `marked`.

Routes (`router/index.ts`):

- `/documents` → `DocumentsListView`
- `/documents/:id` → `DocumentDetailView` (redirects to latest version)
- `/documents/:id/v/:version` → `DocumentDetailView`

All three behind `requiresAuth`. No role gate — the API does scoping.

Nav: add `Documents` link to `components/MainNav.vue` (between Changes and Watchlists) and to `views/AdminLayout.vue` (between Clients and Audit).

API client: extend `src/api/client.ts` with `listDocuments`, `getDocument`, `getDocumentVersion`, `getDocumentClauses`.

Dependencies: add `marked` and `dompurify` if not already in `packages/horizons-webapp/package.json`. Sanitize markdown output before injecting.

## Tests

### Python (`packages/horizons-api/tests/routes/test_documents.py`)

- `test_list_documents_scoped_uk` — login as UK demo, count ≥10, every item has `jurisdiction == "UK"`.
- `test_list_documents_scoped_eu` — same for EU.
- `test_list_documents_admin` — admin sees the full corpus (≥31).
- `test_list_documents_search` — title substring filter.
- `test_get_document_in_scope` — 200 with versions array.
- `test_get_document_out_of_scope_returns_404` — UK client requesting an EU-only doc.
- `test_get_clauses_returns_ordered` — clauses array is sorted by `ord`, paths look right.
- `test_get_clauses_out_of_scope_returns_404` — UK client requesting an EU version's clauses.

### Python (`tests/test_seed_curated_set.py`)

- Bump the count assertion to the new total.
- Add `test_seed_curated_set_uk_subscription_sees_ten` and `test_seed_curated_set_eu_subscription_sees_ten` — run the seeder, set scope to UK demo subscription, count documents.

### Webapp (vitest, `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts`)

- Renders one card per clause.
- Anchor chip shows the full `clause_path`.
- Depth-2 clause is indented deeper than a depth-1 clause.

### Playwright (`packages/horizons-webapp/e2e/documents-viewer.spec.ts`)

- Log in as UK demo.
- Click Documents nav.
- List shows ≥10 rows.
- Open the first row.
- Default view renders body as continuous markdown.
- Click `Show clause structure`.
- Page now shows clause cards with anchor chips.

## File-by-file change list

- `data/curated_set.yaml` — add ~21 entries with UK/EU relabels.
- `packages/horizons-core/src/horizons_core/repos/documents.py` — add `list_with_version_summary(filters, limit, cursor)` returning a new DTO that includes version-count + latest-version-label.
- `packages/horizons-api/src/horizons_api/routes/documents.py` — new router.
- `packages/horizons-api/src/horizons_api/deps/session.py` — new `session_for_request_or_admin` dep.
- `packages/horizons-api/src/horizons_api/app.py` — `include_router(documents.router)`.
- `packages/horizons-webapp/src/api/client.ts` — four new client functions + types.
- `packages/horizons-webapp/src/views/DocumentsListView.vue` — new.
- `packages/horizons-webapp/src/views/DocumentDetailView.vue` — new.
- `packages/horizons-webapp/src/components/documents/DocumentBody.vue` — new.
- `packages/horizons-webapp/src/components/documents/ClauseOverlay.vue` — new.
- `packages/horizons-webapp/src/router/index.ts` — three new routes.
- `packages/horizons-webapp/src/components/MainNav.vue` — `Documents` link.
- `packages/horizons-webapp/src/views/AdminLayout.vue` — `Documents` link.
- `packages/horizons-webapp/package.json` — `marked` + `dompurify` if missing.
- Tests as listed above.
- `docs/api/horizons-primitives.md` — note the new endpoints (or extend `endpoints.md` regeneration — already auto, no hand-edit).
- `journal/260606-wu85-demo-corpus-and-doc-viewer.md` — session notes.

## Verification

Local sweep before merging to main:

```
uv run pytest
uv run ruff check .
uv run pyright
uv run pre-commit run --all-files
cd packages/horizons-webapp && npm run lint:check && npm run build && npm run test:unit -- --run
```

Plus a manual smoke: boot the local stack per `docs/runbooks/local-dev.md`, log in as UK demo, browse documents, open one, toggle structure.
