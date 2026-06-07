# 2026-06-05 — WU5.2: Watchlist management view

*Last revised: 2026-06-05.*
*Path: journal/260605-wu52-watchlists-view.md.*

Track-5 watchlist surface. Built on the WU4.3 `/v1/me/watchlists` endpoints
and the WU4.4 `/v1/discovery` scope-filtered identity feed. Lands together
with WU5.1 (runtime config) in worktree
`wu5.1-5.2-runtime-config-and-watchlists`.

## What shipped

### Route + view

- `src/views/WatchlistsView.vue` — `/watchlists`, `requiresAuth: true`.
  No admin redirect; WU5.4 will add the admin shell that bounces admins
  to their own surface. Header with Home / Changes nav links, page
  title + lead copy, "Add documents" button, empty / loading / error
  states, and a table with one row per watchlist.
- Table columns: Name (defaults to document title when the user doesn't
  supply one), Document ID (mono font, slate), Added (`YYYY-MM-DD`
  slice of `created_at`), Actions (Remove button).
- Linked from `HomeView.vue` next to the "Browse recent changes" link.

### API surface (`src/api/watchlists.ts`)

Plain axios wrappers around the WU4.3 endpoints:

```ts
fetchWatchlists()       → GET    /v1/me/watchlists       → Watchlist[]
createWatchlist(body)   → POST   /v1/me/watchlists       → Watchlist
deleteWatchlist(id)     → DELETE /v1/me/watchlists/:id   → void (204)
```

`Watchlist` mirrors the API's `WatchlistResponse`: `{id, document_id,
name, created_at}` (all strings; `created_at` is the API's ISO-8601
serialisation of the DB `datetime`).

### Discovery feeder (`src/composables/useScopedDiscovery.ts`)

`useScopedDocuments()` wraps `useQuery` around `GET /v1/discovery`
(corpus scope, limit 50) and projects the change-event stream down to a
deduped list of `{document_id, jurisdiction, sector}`. The discovery
endpoint already enforces subscription scope server-side, so any
document the user cannot subscribe to is invisible by construction — the
add dialog has no way to offer an out-of-scope document. The "out-of-
scope guard" test asserts this contract.

### Watchlist composable (`src/composables/useWatchlists.ts`)

Three pieces, one query key:

```ts
export const WATCHLISTS_QUERY_KEY = ['watchlists', 'me'] as const
useWatchlistsQuery()          → useQuery({queryKey: WATCHLISTS_QUERY_KEY, ...})
useAddWatchlistMutation()     → useMutation with onMutate / onError / onSettled
useRemoveWatchlistMutation()  → useMutation with onMutate / onError / onSettled
```

Both mutations follow the standard TanStack Query optimistic pattern
(see the "Cache invalidation strategy" section below for the
conventions future webapp WUs should follow).

### Add / Remove dialogs (`src/components/watchlists/...`)

- `AddWatchlistDialog.vue` — opens on the "Add documents" click. Search
  input filters the deduped discovery list by document id, jurisdiction,
  or sector (case-insensitive, plain `String.includes`). Multi-select
  via per-row checkbox; Cancel / Add buttons in the footer. On confirm,
  fires `mutateAsync` once per selected doc, then resets the dialog
  state and emits `update:open=false`. Per-doc failures don't block
  the rest — `Promise.allSettled` aggregates results, and the toast
  reports "Added N watchlist(s)" and/or "M watchlist(s) failed to add"
  separately. Existing watchlist rows are filtered out of the picker
  (no point offering to add a doc that's already watched).
- `RemoveWatchlistDialog.vue` — confirm dialog. Shows the watchlist
  name in the body; Cancel + Remove (red `bg-red-600`) buttons.

### New shadcn-style UI components

All hand-scaffolded under `src/components/ui/`, matching the WU5.0
pattern (we still haven't run `npx shadcn-vue@latest init` because the
TS alias lives in `tsconfig.app.json`, not the root `tsconfig.json` —
see WU5.0 journal "shadcn-vue init skipped" rationale; that decision
still holds).

| Directory | Files | Built on |
|---|---|---|
| `ui/dialog/` | `Dialog`, `DialogContent`, `DialogHeader`, `DialogFooter`, `DialogTitle`, `DialogDescription`, plus re-exports of reka `DialogClose`, `DialogTrigger` | `reka-ui` Dialog primitives + Tailwind |
| `ui/table/` | `Table`, `TableHeader`, `TableBody`, `TableRow`, `TableHead`, `TableCell` | Raw `<table>` + Tailwind, mirroring shadcn's table shape |
| `ui/toast/` | `ToastViewport` | Backed by `useToast()` composable (see below); not reka-ui's Toast primitives — see decision 4 below |

`useToast()` (`src/composables/useToast.ts`) is a tiny global toast queue:
`success(title, description?)` and `error(title, description?)` push to a
reactive array with a 4-second auto-dismiss. `_resetToasts()` is exported
for test cleanup. The `ToastViewport` component mounted at the bottom of
`WatchlistsView` renders the queue. Variants are `success` (green) and
`error` (red); rendered via `role="alert"` + `aria-live="polite"`.

### Tests (9 new, all using msw)

`src/views/__tests__/WatchlistsView.spec.ts`:

**List + remove (5 tests):**

1. Empty state renders when `GET /v1/me/watchlists` returns `[]`.
2. Loaded state renders one row per watchlist with name + document_id +
   formatted date.
3. **Optimistic remove + 204 → row stays gone**: msw mutates an
   in-handler list state so the post-mutation refetch returns the new
   server state.
4. **Optimistic remove + 500 → rollback + error toast**: msw returns
   500; the spec asserts the row reappears and a `toast-error` is in
   the DOM.
5. List fetch failure renders the `error-state` banner.

**Add dialog (4 tests):**

6. Dialog lists in-scope documents, deduped by `document_id` (three
   change events → two unique documents).
7. Selecting a doc, clicking confirm: POST is sent, dialog closes, the
   list query is invalidated, and the new row appears (msw handler
   appends to its server-state array so the refetch returns the row).
8. **Out-of-scope guard**: a `discovery-checkbox-doc-out-of-scope`
   never appears in the dialog because the discovery handler never
   returns that ID. The contract: discovery is the scope gate.
9. Search input filters the picker by document ID / jurisdiction /
   sector (case-insensitive).

Test plumbing detail: Reka-ui's `DialogPortal` teleports content to
`document.body`, so `wrapper.find` (which scans the wrapper's own DOM
only) cannot reach dialog contents. Tests use a `inPortal(selector)`
helper that wraps `document.querySelector`. `attachTo: document.body`
on `mount` keeps the wrapper's root in the live document so click
events on table rows still reach the right handlers.

## Cache invalidation strategy

Convention for future Track-5 work:

1. **One query key per resource family.** Watchlist lives at
   `WATCHLISTS_QUERY_KEY = ['watchlists', 'me'] as const`. The
   `as const` keeps TanStack's key-equality checks happy.
2. **Mutations invalidate exactly the keys they touch.** Both add and
   remove call `queryClient.invalidateQueries({ queryKey:
   WATCHLISTS_QUERY_KEY })` in `onSettled`. No cross-family
   invalidation — adding a watchlist does not invalidate
   `['discovery', ...]` because the watched document doesn't disappear
   from discovery just because the user starts watching it.
3. **Optimistic + rollback shape**:
   ```ts
   onMutate: async (input) => {
     await queryClient.cancelQueries({ queryKey: KEY })          // halt in-flight refetches
     const previous = queryClient.getQueryData<T>(KEY)            // snapshot for rollback
     queryClient.setQueryData<T>(KEY, (old) => optimistic(old))   // apply
     return { previous }                                          // → context
   },
   onError: (_err, _input, context) => {
     if (context?.previous !== undefined) {
       queryClient.setQueryData(KEY, context.previous)            // restore snapshot
     }
   },
   onSettled: () => {
     void queryClient.invalidateQueries({ queryKey: KEY })        // refetch authoritative
   }
   ```
4. **Optimistic placeholder shape**: for adds, the optimistic row uses
   `id: \`optimistic-${input.document_id}\`` so the DOM key is stable
   across the placeholder → server-confirmed transition (the row
   re-renders rather than re-mounting). Name defaults to "Adding…"
   when the user hasn't supplied one — the API's name-default logic
   (use the document title) only resolves server-side, so the
   placeholder cannot pre-compute it.
5. **Two consecutive `flushPromises()` in tests** when asserting on
   the post-`onSettled` refetch state: one flushes the mutation
   completion + onSettled invalidation; the second flushes the
   triggered refetch.

Query keys registered as of WU5.2:

| Key | Owner | Notes |
|---|---|---|
| `['watchlists', 'me']` | `useWatchlistsQuery` | invalidated by add + remove mutations |
| `['discovery', 'scoped-documents']` | `useScopedDocuments` | not invalidated by watchlist mutations (see point 2 above) |
| `['changes', 'discovery', 'corpus']` | `useChangeEvents` (WU5.3) | infinite-query; untouched |
| `['differential', eventId]` | `useDifferential` (WU5.3) | per-event; untouched |

## Decisions worth keeping

1. **Query key for the add-dialog discovery feed is
   `['discovery', 'scoped-documents']`, NOT `['changes', ...]`.** The
   change-list view's `useChangeEvents` uses
   `['changes', 'discovery', 'corpus']` for an infinite query. Sharing
   would cross-pollute cache shapes (Page vs flat array). Distinct
   keys, distinct stalenesses.
2. **`Promise.allSettled` for multi-doc adds, not `Promise.all`.** If
   the user picks 3 documents and one POST 500s, the other two should
   still land. The toast summarises (`Added 2, 1 failed`) rather than
   forcing the user to retry the whole selection.
3. **Existing watchlist rows are filtered out of the add dialog.** A
   user shouldn't be offered the option to "watch" something they
   already watch — and the UNIQUE constraint on `(user_id,
   document_id)` from WU4.3 would 4xx the duplicate anyway.
4. **Custom `useToast()` composable, NOT reka-ui's Toast primitives.**
   Reka's Toast requires a ToastProvider tree + Portal + viewport
   wiring; for the demo, two coloured banner shapes (success/error)
   with a global queue is a 60-line file that covers every case
   the WU5.2 mutations need. Reka's Toast remains an option if
   later WUs need richer interactions (actions, swipe-to-dismiss).
5. **Dialog `data-testid` lives on a child `<div>` inside
   `<DialogContent>`, not on `<DialogContent>` itself.** Reka's
   `DialogContent` renders through a `<DialogPortal>` and the
   teleport / fragment root drops non-prop attributes (Vue warns
   loudly). Putting the testid on an inner div is the conventional
   workaround.
6. **204 from DELETE returns plain `void`** (the axios call is awaited
   but its response body discarded). The mutation success path doesn't
   need to read anything back — the optimistic update already removed
   the row and `onSettled` triggers an authoritative refetch.
7. **Remove confirms via a dedicated dialog, not a `window.confirm()`
   browser prompt.** Browser confirms blink in vitest/jsdom and are
   easier to write tests against, but they look hostile in a polished
   demo and don't match the rest of the SPA's shadcn-styled surfaces.

## Verification gate

Same shape as WU5.1 — the two work units land together. From the
worktree:

```bash
uv run ruff check . && uv run pyright && uv run pytest -m "not integration" && uv run pre-commit run --all-files
# → ruff clean, pyright 0 errors, 323 passed, pre-commit clean

cd packages/horizons-webapp
npm run lint:check     # oxlint + eslint clean
npm run test:unit -- --run   # 134 tests across 17 files, all pass (+18 from WU5.1+5.2 vs the 116 baseline)
npm run build          # vue-tsc + Vite, 0 TS errors
```

## Manual verification (for the user)

The in-session unit/component tests cover the wire shape. The
end-to-end check:

1. Start the API + apply WU4.3 migration: `uv run alembic upgrade
   head && uv run uvicorn horizons_api.app:app --reload --port 8000`.
2. Seed a client user with an active subscription scope (per WU5.0's
   journal — there's no first-class `scripts/create_user.py` yet;
   the integration-test seeding pattern in
   `tests/test_me_and_watchlists_endpoints.py` is the recipe).
3. Start the webapp: `cd packages/horizons-webapp && npm run dev`.
4. Log in. Click "Manage watchlists" on the Home view (or navigate to
   `/watchlists` directly).
5. **Empty → add**: click "Add documents". Expected: dialog opens,
   discovery API call fires (`GET /v1/discovery?scope=corpus&limit=50`),
   the in-scope document list renders. Tick one, click "Add 1".
   Expected: dialog closes, the row appears in the table, toast says
   "Added 1 watchlist".
6. **Add multiple**: re-open, tick 2 + 3 documents, confirm. Expected:
   two/three POSTs (visible in DevTools), all land, toast says
   "Added N watchlists".
7. **Search filters**: open dialog, type a jurisdiction code in the
   search input. Expected: the picker list shrinks to just rows whose
   doc id / jurisdiction / sector text contains the query.
8. **Already-watched is hidden**: with a watchlist for doc X already
   present, open the dialog. Expected: X is NOT in the picker.
9. **Remove + 204**: click a row's Remove. Expected: confirm dialog
   shows the watchlist name; click Remove. Row disappears (optimistic);
   network shows DELETE → 204; refetch leaves the row gone; success
   toast.
10. **Remove + 5xx rollback**: temporarily stop the API mid-flight (or
    use a fault-injection proxy). Expected: row reappears after the
    DELETE fails; error toast says "Could not remove watchlist".
11. **Out-of-scope smoke**: use an admin or DB shell to assign the
    user a single jurisdiction. Re-load `/watchlists`. Expected: the
    add dialog only offers documents in that jurisdiction.

## Track 5 status (after WU5.2)

| WU | Status |
| --- | --- |
| WU5.0 | shipped |
| WU5.1 | shipped this session — see `260605-wu51-runtime-config.md` |
| **WU5.2** | **shipped (`/watchlists` route, add/remove dialogs, Dialog/Table/Toast components, optimistic mutations)** |
| WU5.3 | shipped |
| WU5.4 | next — admin views (depends on WU4.5 + WU5.2) |
| WU5.5 | shipped |
| WU5.6 | shipped |

## Cadence note

Both WU5.1 and WU5.2 ship together via fast-forward merge into `main`
per `CLAUDE.md`'s CI / merge cadence — they share a worktree, so the
two-unit landing is one ff-merge, one push.
