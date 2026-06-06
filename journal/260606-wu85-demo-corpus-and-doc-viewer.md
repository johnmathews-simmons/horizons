# WU8.5 — Demo corpus expansion + document viewer

**Branch:** `worktree-wu85-demo-corpus-and-doc-viewer`. Direct fast-forward
into `main` per the WU8.4 cadence.

## What landed

1. **Seeded the full fixture inventory.** `data/curated_set.yaml` now lists
   every fixture in `data/samples/` (31 documents). `data/samples/fixtures.json`
   gained the missing `IE-27732019` row so `select()` matches it; without
   the fixtures-inventory entry the YAML override is silently dropped with a
   `not found in fixture inventory` warning.

2. **Demo subscription scopes resolve ≥10 documents each.**
   `demo-uk@demo.example.com` (UK/BANKING) and `demo-eu@demo.example.com`
   (EU/BANKING) each see 10 docs after the relabel cluster. The remaining 11
   fixtures keep their native ISO-2 jurisdiction and are admin-only. The
   sector is forced to `BANKING` on every relabel — the demo subscriptions
   subscribe to BANKING only, so honest-sector mix is sacrificed for visible
   list density.

3. **New `/v1/documents` router** at `packages/horizons-api/src/horizons_api/routes/documents.py`.
   Three GETs: list (with `jurisdiction`/`sector`/`search`/`limit`/`offset`),
   detail-with-versions, and a flat ordered clauses bundle. All three are
   scope-aware through the new `session_for_request_or_admin` dependency:
   clients run under `api_app` (RLS-filtered); admins run under
   `admin_operator_session` (`admin_bypass` role + one `admin_access_log`
   row per request). The same handler serves both roles — the only
   difference is which rows come back.

4. **Documents viewer in the webapp.** Three new files under
   `packages/horizons-webapp/src/`:
   - `views/DocumentsListView.vue` — search + jurisdiction + sector
     filters, URL-synced.
   - `views/DocumentDetailView.vue` — title header, version label, and
     the `Show clause structure` toggle.
   - `components/documents/ClauseOverlay.vue` — single component for
     both modes. `showStructure=false` renders the clauses as continuous
     body text in a serif `<pre>` block; `showStructure=true` renders
     each clause as a depth-indented card with the parser-assigned anchor
     path (`PART_2/SECTION_4/(a)/(i)`) as a chip on top.
   - Routes added at `/documents`, `/documents/:id`, `/documents/:id/v/:version`.
   - Nav links added to `HomeView`, `ChangesView`, `DocumentsListView`
     (client-facing) and `AdminLayout` (admin-facing).

5. **Tests across three layers:**
   - 8 integration cases in `tests/test_documents_endpoints.py` covering
     in-scope listing, search/jurisdiction filters, admin-sees-all,
     detail-with-versions, out-of-scope 404, ordered clauses, and
     out-of-scope clauses 404.
   - Vitest spec at `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts`
     covering both modes, anchor chips, depth-indent ordering, and the
     empty-list case.
   - Playwright e2e at `packages/horizons-webapp/e2e/documents-viewer.spec.ts`
     drives the UK demo through the full flow including the structure
     toggle. The e2e seed (`packages/horizons-api/scripts/seed_e2e.py`)
     was extended to insert a few clauses per document so the toggle has
     something to render; the existing teardown already deletes clauses.

## Decisions worth pinning

- **No markdown library.** `ChangeDetailView` renders clause text as plain
  text via `DiffView`; matching that pattern (raw text in `<pre>` blocks)
  avoided pulling in `marked` + `dompurify`. If the demo later needs
  richer rendering — headings, bold, lists — revisit.
- **Sector-flattened relabels.** Every UK/EU relabel forces `sector:
  BANKING` because the demo subscriptions subscribe to BANKING only. The
  curated_set comment block notes this loss of editorial honesty as a
  deliberate demo trade-off; the admin-only block keeps original sectors.
- **Same handler for both roles.** `session_for_request_or_admin` switches
  the database role based on the principal. The alternative was a
  parallel `/admin/documents` tree; rejected because the wire shape is
  identical and the audit trail still fires under the existing
  `admin_operator_session` bracket.
- **404 on out-of-scope.** Mirrors the primitives surface — a client
  cannot distinguish "not found" from "not in your subscription scope."
  Documented in `routes/documents.py`.

## Re-seeding the staging corpus

The deployed `horizons-nonprod` corpus was seeded from the pre-WU8.5
`curated_set.yaml` and is locked-in — `documents` is append-only via
trigger. To see the new 31-document set in staging, dispatch the existing
re-seed Job:

```
scripts/reseed_aca.sh --yes
```

The Job re-runs `seed_curated_set.py` against the staging DB. Documented
in `docs/runbooks/reseed.md`.

## What's NOT in this WU

- Clause-diff highlighting in the viewer (Changes view still owns the
  diff beat).
- Richer text rendering than plain-text-in-`<pre>`.
- A `/v1/documents/{id}/versions/{label}` metadata-only endpoint (not
  needed by the webapp; the detail endpoint already returns the version
  list and the clauses endpoint covers per-version reads).
