# 2026-06-05 — WU5.3: Change-browsing view + clause diff render

*Last revised: 2026-06-05.*
*Path: journal/260605-wu53-change-diff-view.md.*

The demo's headline UX. List of recent change events, click into a
clause diff. Worktree `eng-wu5.3-change-diff-view`. Three commits on
top of `23a238f`:

1. `ac3028e` — webapp `/changes` list view + composable + filters + badge/pill
2. `d870f0a` — backend `GET /v1/differential/{event_id}` for single-event lookup
3. `dc202c9` — webapp `/changes/:id` clause-diff view + diff renderer

## Decisions taken up-front

Recommended options were accepted on all three open questions:

1. **Per-event fetch shape**: added `GET /v1/differential/{event_id}` as
   a backend addition rather than overload clause-scope filtering or
   pre-fetch caching. Clean UX, direct deep-links work, RLS still
   enforces visibility — one extra repo method + route + 4 tests.
2. **Diff render strategy**: manual `<ins>` / `<del>` spans wrapping a
   typed facade over `diff_match_patch`, not `diff_prettyHtml`. Lets
   Tailwind own the styling and gave us side-by-side without
   re-implementing diff alignment.
3. **Pagination**: "Load more" button via TanStack `useInfiniteQuery`.
   Predictable, accessible, easy to test, matches the cursor's
   append-only shape.

Other questions were resolved without asking. TanStack Vue Query was
already installed and bootstrapped in `main.ts` (good). `/config.json`
runtime config (WU5.1) isn't shipped yet, so confidence thresholds
went into `src/constants/confidence.ts` with a `TODO(WU5.1)` for the
externalisation later. Commit split followed the spec recommendation.

## What shipped

### Backend — `GET /v1/differential/{event_id}` (commit 2)

- New repo method `ChangeEventsRepository.get_by_id(event_id)` returns
  `ChangeEventDTO | None`. Plain `SELECT … WHERE id = :id` —
  subscription-scope filtering is handled entirely by the
  `change_events_in_scope` RLS policy, exactly like the bulk endpoints.
  Out-of-scope rows come back as `None`.
- New route `differential_by_id` reuses the existing
  `_to_differential_item` projection helper and the `_no_store`
  cache-control marker. `include_content` defaults `true` — a single
  bounded event, no corpus-style payload guard applies.
- 404 covers both "doesn't exist" and "exists but invisible via RLS".
  Same status, indistinguishable by design — the wire contract update
  in `docs/api/horizons-primitives.md` calls this out explicitly so
  the webapp's "not in your subscription scope" copy reads as expected
  behaviour rather than a bug.
- Tests in `tests/test_primitives_endpoints.py` cover bearer required,
  in-scope happy path with text, `include_content=false` strips text,
  out-of-scope → 404 (same as nonexistent), nonexistent → 404.

### Webapp — primitives + components (commit 1)

- `src/api/changes.ts` — `fetchDiscovery({ cursor, limit })` against
  `/v1/discovery?scope=corpus`, with `DiscoveryItem` / `DiscoveryPage`
  typed from the wire contract.
- `src/composables/useChangeEvents.ts` — wraps `useInfiniteQuery`. The
  `getNextPageParam` short-circuits when either `has_more` is false or
  `next_cursor` is missing so the Load-more button hides cleanly on
  the last page.
- `src/components/ui/confidence-badge/` — two-decimal float, tiered
  red / amber / green via `confidenceTier(value)`. Tier is exposed
  via `data-confidence` so the filter logic and tests can target it
  without re-deriving from class names.
- `src/components/ui/change-type-pill/` — coloured pill per change
  type. Same `data-change-type` pattern for selectable styling /
  testing.
- `src/views/ChangesView.vue` — header, filters, list, Load-more,
  empty / loading / error states. Default-off toggles suppress MOVED
  events and below-threshold confidences (`<` 0.6); switching either
  toggle on reveals them without re-fetching.
- Row renders jurisdiction · sector · change-type pill · path lozenge
  (with `before → after` when MOVED's path differs) · relative
  timestamp · confidence badge. The wire shape doesn't carry a
  document title yet — the discovery rows are document-id-only, so
  the row's primary text is the clause path, not a doc title. A
  post-demo TODO if the join becomes worth the latency.

### Webapp — detail view + diff renderer (commit 3)

- `diff-match-patch` + `@types/diff-match-patch` added as runtime
  deps. The library is 15 years old with `any`-flavoured types, so
  the integration point is a one-file facade.
- `src/lib/diff.ts` — `computeDiff(before, after) → DiffOp[]` where
  `DiffOp = { op: -1 | 0 | 1; text: string }`. Nulls are coerced to
  empty strings so ADDED / REMOVED collapse to single-op diffs
  naturally. `diff_cleanupSemantic` runs to merge tiny adjacent edits
  into human-readable spans.
- `src/components/ui/diff-view/DiffView.vue` — accepts
  `before / after / mode`. Default `side-by-side` paints two columns
  (left filters out inserts, right filters out deletes); the
  `unified` toggle paints one column with `<ins>` / `<del>` spans
  interleaved. Each column is `<pre class="whitespace-pre-wrap">` so
  long clauses wrap rather than scroll horizontally. Vue's text
  interpolation handles escaping — no `v-html`, no XSS surface.
- `src/composables/useDifferential.ts` — wraps `useQuery` keyed on
  `['differential', eventId]`. The key includes the ref so the cache
  stays consistent across navigations.
- `src/views/ChangeDetailView.vue` — header (pill, path lozenge,
  jurisdiction / sector, confidence badge, mode toggle), `<DiffView>`,
  "← All changes" link. Distinguishes 404 ("not in your subscription
  scope") from generic error ("could not load").

### Tests

- 86 tests across 11 webapp files (was 68 / 8 before this WU).
  `ConfidenceBadge`, `ChangeTypePill`, `DiffView`, `computeDiff`,
  `ChangesView`, `ChangeDetailView` are all covered. View-level
  integration tests use msw to mock the API, a fresh `QueryClient`
  per mount, and a `createMemoryHistory` router.
- 5 new backend integration tests in
  `tests/test_primitives_endpoints.py` for the by-id route. Full
  Python sweep stays at 516 passed / 4 skipped / 90% coverage.

## Gotchas worth remembering

1. **Checkbox `v-model` + `.trigger('click')` doesn't update the
   bound state in `@vue/test-utils`.** Use `.setValue(true)` /
   `.setChecked()`. Cost me one test failure.
2. **`wrapper.get(selector).exists()` is a type error** — `get`
   returns `Omit<DOMWrapper, "exists">`. Use `wrapper.find(...).exists()`
   for the existence check (or just drop `.exists()` after `.get()`
   since `get` throws on miss). Hit this twice in the same session.
3. **`oxlint`'s `no-conditional-expect`** fires inside msw handlers
   if you `expect()` from a conditional branch. Capture request
   state into an array inside the handler, assert on it after the
   action — that's the pattern.
4. **TanStack Vue Query already in `main.ts`** — no plugin re-add
   needed for new views, but new tests must mount with the
   `VueQueryPlugin` *and* a fresh `QueryClient` per mount to avoid
   cross-test cache bleed.
5. **The webapp build emits a `/* #__PURE__ */` warning from
   `@vueuse/core/dist/index.js`.** Rolldown can't interpret the
   comment's position; this is upstream noise, not ours.

## Wire contract addition

`docs/api/horizons-primitives.md` now has a "Single-event lookup"
section and the Errors section explicitly notes that 404 returns
only from `/v1/differential/{event_id}` — list endpoints never 404
since out-of-scope rows are silently absent from the page. This is
the only contract change in this WU.

## Follow-ups (post-demo)

1. Externalise confidence thresholds to `/config.json` once WU5.1
   ships. `src/constants/confidence.ts` has the `TODO(WU5.1)`.
2. Add a document-title join to the discovery wire shape so the
   change-list row leads with the act name rather than the clause
   path. Bounded API change; post-demo unless the demo audience
   asks for it.
3. Property-test `computeDiff` via fast-check (per the
   `project_horizons_post_demo_fastcheck` memory). The wire-shape
   boundaries are well-covered by example-based tests already; the
   property tests would buy us regression confidence on weird
   Unicode / long-clause edges.
4. Large-doc rendering safety (WU5.5) layers `@tanstack/vue-virtual`
   over the change-list and moves diff computation to a Web Worker
   for documents > 1 MB. Manual test target: the 3.8 MB AL fixture.
