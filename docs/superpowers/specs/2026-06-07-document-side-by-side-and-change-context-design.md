# Document side-by-side viewer + change-in-context — design

*Last revised: 2026-06-07.*
*Path: docs/superpowers/specs/2026-06-07-document-side-by-side-and-change-context-design.md.*

**Status:** draft, pending implementation.
**Author:** John (via Claude).
**Date:** 2026-06-07.
**Demo deadline:** 2026-06-08.

## 1. Problem

Two related demo-day UX gaps in the webapp:

1. **Document detail viewer hides version history.** `DocumentDetailView.vue`
   today picks the latest version and renders its clauses in a single pane.
   For documents with both a v1 and a synthetic v2 (the demo's headline
   diff fixtures: GB 28914588, IE 27732019, AU 2145602, DE 20951816,
   FR 31702142, IT 26863, EU 31366184), there is no way to see the
   previous version, no way to compare the two, and no indication of
   when Horizons first ingested each.
2. **"Recent Changes" click target is an isolated clause snippet.**
   `ChangeDetailView.vue` renders only the before/after text of the
   changed clause via `DiffView`. With no surrounding document, the page
   reads as a stub — the change is detached from the context that makes
   it meaningful. The user's intuition: clicking a change should land on
   the full document with the changed clause highlighted in place.

Both gaps land on the same surface — the side-by-side document viewer —
so this design covers them together.

## 2. Scope

Webapp only. The Horizons API already returns everything needed:

- `GET /v1/documents/{id}` includes `versions: [{ id, version_label,
  publication_date, effective_date, content_bytes, created_at }, ...]`.
  `created_at` is the row's insertion timestamp — when Horizons first
  ingested the version. That is "first seen".
- `GET /v1/documents/{id}/versions/{label}/clauses` returns the
  `ClauseBundle` for any version.
- `GET /v1/discovery` items carry `document_id`, `document_version_id`,
  `before_path`, `after_path`. Enough to link from a change row to
  the document at the right anchor.

No API changes, no migrations, no ingestion changes.

## 3. Design

### 3.1 `DocumentDetailView` restructure

- **Single-version docs:** behaviour unchanged. Single-pane
  `ClauseOverlay`, current header.
- **Multi-version docs:** default to a two-pane side-by-side layout.
  - **Pane order.** Versions sorted by `effective_date ?? created_at`
    ascending. Oldest on the left, latest on the right. v1 left, v2
    right for the demo corpus.
  - **Per-pane header.** Shows `version_label · seen <created_at,
    date only>`. ISO `YYYY-MM-DD` is fine — no relative-time
    formatting for the version header.
  - **Pane body.** Each pane runs an independent `ClauseOverlay`
    bound to that version's `ClauseBundle`. The existing "Show clause
    structure" toggle in the page header applies to both panes
    simultaneously.
  - **Scrolling.** Each pane scrolls independently. No sync-scroll
    in v1. Visually-misaligned MOVED clauses across panes are
    expected and informative.
  - **Width.** Two equal-width columns on `md+` viewports; stack
    vertically below the `md` breakpoint (the demo runs on desktop,
    but the stack fallback is cheap to keep the e2e + Vitest
    snapshots stable across viewport sizes).
  - **Loading + error states.** Each pane carries its own
    pending/error indicator. One pane failing does not block the
    other from rendering. Both panes' queries fire in parallel
    (`useQuery` per pane, distinct query keys).
- **Page header (above the pane strip).**
  - Document title, jurisdiction, sector (existing).
  - The single-version `version <label>` line is replaced when there
    are ≥2 versions; the per-pane headers carry that information now.
  - The "Show clause structure" toggle stays at the page level.

### 3.2 Change-in-context highlight

- **New URL contract on `/documents/{id}`.** Optional query params:
  - `before=<clause_path>` — URL-encoded `clause_path` to highlight
    in the left (older) pane.
  - `after=<clause_path>` — URL-encoded `clause_path` to highlight
    in the right (newer) pane.
- **Auto-scroll on mount.** When `before` or `after` is present, the
  matched clause card in each pane is brought into view via
  `element.scrollIntoView({ block: 'center', behavior: 'auto' })`
  once its `ClauseBundle` resolves. No custom offset math.
  - Left pane scrolls to the clause matching `before_path`.
  - Right pane scrolls to the clause matching `after_path`.
  - For pure ADDED (no `before_path`): left pane scrolls to the
    nearest neighbour by `ord` on the right pane's clause
    (degenerate but acceptable for the demo — the user's attention
    is on the highlighted right-pane clause).
  - For pure REMOVED (no `after_path`): symmetric — right pane
    scrolls to the nearest neighbour.
- **Highlight treatment.** The matched clause card gets a
  contrasting ring + a tinted background. Highlight is persistent —
  no auto-dismiss after a timeout. Clears when:
  - The user navigates away.
  - The query params are removed from the URL.
- **Edge cases.**
  - Highlight path not found in the clause bundle (e.g. demo data
    drift): pane renders without highlight, no error. Console
    `console.warn` for diagnostics; no user-facing message.
  - Document has only one version but the URL carries `before`/
    `after`: the params are honoured against the single pane (left
    or right per which param is present); the layout stays
    single-pane.

### 3.3 Recent Changes navigation

`ChangesView.vue` currently routes change rows to
`{ name: 'change-detail', params: { id: row.item.id } }`. Replace with:

```ts
{
  name: 'document-detail',
  params: { id: row.item.document_id },
  query: {
    before: row.item.before_path ?? undefined,
    after: row.item.after_path ?? undefined,
  },
}
```

`null` paths are dropped from the query (rather than serialised as
the literal string `"null"`), so the URL is clean for ADDED/REMOVED
events.

### 3.4 Retirements

- `packages/horizons-webapp/src/views/ChangeDetailView.vue` — delete.
- `packages/horizons-webapp/src/views/__tests__/ChangeDetailView.spec.ts` — delete.
- `packages/horizons-webapp/src/composables/useDifferential.ts` — delete.
- `packages/horizons-webapp/src/api/changes.ts` — drop
  `DifferentialItem` and `fetchDifferentialById`. Keep `DiscoveryItem`
  + `fetchDiscovery`.
- `packages/horizons-webapp/src/components/ui/diff-view/` — delete the
  directory (`DiffView.vue`, `index.ts`, `__tests__/`).
- `packages/horizons-webapp/src/router/index.ts` — remove the
  `change-detail` route.

The differential primitive endpoint (`/v1/differential/{id}`) stays
on the API. It is a documented primitive (`docs/api/horizons-
primitives.md`), and removing it would touch the API surface and the
public docs. No UI calls it after this change. Acceptable.

### 3.5 Tests

- **New Vitest specs.**
  - `DocumentDetailView.spec.ts` (extend the existing spec if there
    is one, otherwise new):
    - Single-version doc renders single-pane (regression).
    - Two-version doc renders both panes with correct
      `version_label · seen <date>` headers, oldest left.
    - Per-pane loading state isolates to one pane.
    - URL `?before=...&after=...` scrolls + highlights the matching
      clause cards in both panes. Use `scrollIntoView` spy /
      jsdom-friendly equivalent.
    - URL `?after=...` only (ADDED-shape): right pane highlights,
      left pane does not.
    - Missing path in query: pane renders without highlight,
      `console.warn` called.
- **Changed Vitest specs.**
  - `ChangesView.spec.ts:` update the click-target assertion to
    expect `name: 'document-detail'` with the right query params.
- **Deleted Vitest specs.**
  - `ChangeDetailView.spec.ts`.
- **e2e (`packages/horizons-webapp/e2e/`).**
  - Audit `login-and-scope.spec.ts` and any spec that asserts a
    `change-detail` URL or clicks through to one. Update to assert
    the new `/documents/{id}?before=...&after=...` URL and that
    the highlighted clause is visible.
  - `documents-viewer.spec.ts` (existing, WU8.6) keeps asserting
    ≥1 parsed clause per visible document. Should keep passing
    against the new layout — the per-pane `ClauseOverlay` still
    emits `data-testid` clause cards.

### 3.6 Out of scope

- Sync-scroll between panes.
- Drawing clause-pair alignment lines / inter-pane connectors.
- A "compare arbitrary versions" picker (only meaningful at ≥3
  versions; the demo corpus has at most 2 per document).
- Changes to the Recent Changes list shape itself (rows still
  render the path + chips + confidence badge).
- Changes to the Browse Documents list shape.
- Re-seeding the staging corpus. The deployed `horizons-nonprod`
  data is WU8.5-shape; picking up the WU8.6 v1 stagings + new
  synthetic v2 pairs requires `scripts/reseed_aca.sh --yes`. That
  is an operator step, separate from this UI work.

## 4. Non-goals

- Replacing the `/v1/differential/{id}` primitive on the API.
- Changing how clauses are parsed or aligned.
- Changing the `change_events` schema or the discovery cursor
  contract.

## 5. Open questions

None at design time. The two decisions taken during brainstorming:

- **Always side-by-side when >1 version** (not a toggle, not a tab
  strip).
- **Retire `ChangeDetailView` outright** (not "keep with a deep
  link").

## 6. Files touched (summary)

- Modify:
  - `packages/horizons-webapp/src/views/DocumentDetailView.vue`
  - `packages/horizons-webapp/src/views/ChangesView.vue`
  - `packages/horizons-webapp/src/router/index.ts`
  - `packages/horizons-webapp/src/api/changes.ts`
  - `packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts`
  - `packages/horizons-webapp/e2e/login-and-scope.spec.ts` (audit
    + update if it asserts the `change-detail` route)
- Add:
  - `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts`
    (or extend an existing one).
- Delete:
  - `packages/horizons-webapp/src/views/ChangeDetailView.vue`
  - `packages/horizons-webapp/src/views/__tests__/ChangeDetailView.spec.ts`
  - `packages/horizons-webapp/src/composables/useDifferential.ts`
  - `packages/horizons-webapp/src/components/ui/diff-view/` (entire
    directory).
