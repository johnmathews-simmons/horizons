# 2026-06-05 — WU5.5: Large-doc rendering safety

The two main-thread risks the demo carries: diff computation against
clauses from the AL fixture's blank-line blocks (up to ~88 KB each),
and rendering a /changes list once ingestion backlog catches up and
the API returns hundreds of rows per page. WU5.5 retires both.

Worktree `eng-wu5.5-large-doc-safety`. Two commits on top of
`3163e49` (WU4.5 secfix):

1. `1f5c042` — `computeDiffAsync` threshold dispatcher + Web Worker +
   DiffView async wiring + computing skeleton.
2. `b925bf5` — `@tanstack/vue-virtual` windowing the `/changes` list.

## Decisions taken up-front

All four open questions accepted as recommended:

1. **Row sizing**: variable height via TanStack's `measureElement`.
   The row content varies meaningfully (MOVED rows wrap with
   `before → after` paths), and the alternative — fixed height with
   single-line truncation — would silently drop information.
2. **Diff dispatch threshold**: `before.length + after.length > 50_000`.
   The spec wording was "documents > 1 MB" but the diff input is
   clause-level, not doc-level. Probed the AL fixture — its largest
   blank-line blocks are ~88 KB, so a clause-vs-clause diff can hit
   ~170 KB combined. 50 K is the crossover where the postMessage
   round-trip becomes cheaper than blocking the main thread; keeps
   the sync path covering everyday small edits without Promise tax.
3. **Worker bundling**: Vite's `?worker` query import. Standards-shaped
   variant works too; `?worker` is the first-class pattern, no friction
   with the Vue plugin / vue-tsc, and the call site is one line.
4. **Skeleton copy**: a single-line "Computing diff…" pane in the same
   container shape as the future panes, matching the existing
   "Loading clause diff…" wait state from ChangeDetailView.

## What shipped

### Worker boundary (commit 1)

- `src/lib/diff.ts` — `computeDiffAsync(before, after, opts?)` returns
  `{ promise: Promise<DiffOp[]>, cancel(): void }`. Below
  `DIFF_WORKER_THRESHOLD` (50 000 chars combined) it falls through
  to the existing sync `computeDiff` wrapped in `Promise.resolve` —
  same shape, no microtask tax beyond the resolve itself. Above
  threshold it spawns a one-shot Worker via Vite's `?worker` import,
  terminates it on reply / error / cancel, and an internal `settled`
  flag idempotents `cancel-after-resolve`.
- `src/workers/diff.worker.ts` — receives `{ before, after }`, runs
  `computeDiff`, posts `{ ops }` back. The message handler is
  exported as `handleDiffMessage` so it can be unit-tested via
  direct import (jsdom doesn't ship `Worker`). The `self.onmessage`
  side-effect is guarded by `typeof window === 'undefined'` so the
  test import doesn't clobber a window-scoped handler.
- `src/workers/README.md` — short doc explaining the threshold,
  lifecycle, and why a worker is the right tool here (diff-match-patch
  is a single tight loop with no natural yield point).
- `DiffView.vue` — keeps the sync path for under-threshold inputs
  (the existing computed-ref pattern is untouched for small clauses).
  Over threshold, a watcher kicks off `computeDiffAsync`, shows a
  "Computing diff…" pane while pending, and replaces it with the
  side-by-side / unified panes when ops land. Stale workers are
  cancelled on prop change and on unmount.

### Virtual list (commit 2)

- `ChangesView.vue` — `useWindowVirtualizer` with `estimateSize: 72`,
  `overscan: 8`, `scrollMargin` tracking `listContainerRef.offsetTop`
  so absolutely-positioned rows land at the right document-Y. The
  page scrolls naturally; no inner overflow container.
- `measureRow()` guards against `getBoundingClientRect().height <= 0`.
  In jsdom that's the default for unrendered elements; feeding 0 back
  into the size cache collapses the visible range to "render
  everything" and defeats the windowing. In production it's a no-op
  on the first paint, and subsequent measurements feed real heights.
- All testids preserved exactly (`change-row`, `toggle-moved`,
  `toggle-below-threshold`, `load-more`, `data-confidence`,
  `data-change-type`). `divide-y` doesn't survive absolute
  positioning, so per-row `border-b border-slate-200` on every row
  except the last gives the same visual separator.

### Tests

- 16 new webapp tests (102 / 102 total, was 86):
  - 8 new `computeDiffAsync` specs in `src/lib/__tests__/diff.spec.ts`
    cover sync-path / worker-path / threshold-edge / null-coercion /
    cancel-before-reply / cancel-after-reply / error-rejects /
    threshold-value-is-50000.
  - 4 worker handler specs in `src/workers/__tests__/diff.worker.spec.ts`
    cover the same shape contract as `computeDiff`.
  - 3 new DiffView specs cover the "Computing diff…" skeleton, the
    unmount-cancel, and the stale-cancel-on-props-change paths via
    a `vi.mock` of `@/lib/diff`.
  - 1 new ChangesView spec asserts that 1000 returned items yield
    far fewer than 1000 rendered rows.
- Full Python sweep stays at 323 passed / 4 skipped / 0 failed in the
  fast (`-m 'not integration'`) selection. ruff + pyright clean.
  pre-commit (including the new endpoints-md drift gate from WU4.6)
  clean.

### Manual smoke

Ran computeDiff against a real 80 KB / 80 KB slice of
`data/samples/al-31592917-v1.md` with a synthesised insert. 160 KB
combined input → 4 ops produced (1 insert, 1 delete, 2 equals) in
1.7 ms on the test machine. Confirms the algorithm completes
quickly on real Albanian text and the output shape matches what the
worker contract expects.

The actual browser-Worker boundary is verified at build time (Vite
processes the `?worker` import and emits the worker bundle) and
will be exercised by the WU8.2 Playwright E2E test against the demo
data. The unit tests cover every layer below that — handler,
dispatcher with fake Worker, DiffView wiring — so the live boundary
is the only contract not exercised under vitest.

## Gotchas worth remembering

1. **jsdom + vue-virtual + 0-height measurements**: the first
   bare-minimum windowing test rendered 509 of 1000 rows because
   `measureElement` accepted the jsdom-default 0-height rect and
   collapsed the size cache. Guard with
   `if (el.getBoundingClientRect().height <= 0) return` and the
   virtualiser falls back to `estimateSize`. In production the
   first real paint feeds the cache properly. Worth knowing
   before reaching for other vue-virtual surfaces (data tables,
   admin lists).
2. **`vi.fn()` always needs an explicit type parameter** to satisfy
   `eslint-plugin-vitest(require-mock-type-parameters)`. Idiomatic
   pattern: `vi.fn<() => void>()` for cancel-style mocks,
   `vi.fn<typeof somemodule.someFunction>()` to mirror the full
   signature of a mocked function. Hit 10 errors at lint time and
   the fixes were mechanical.
3. **`vi.mock` factory typing**: the lazy-arg-spread pattern
   `(...args: Parameters<typeof actual.fn>) => mock(...args)`
   trips TS when `actual.fn` has optional trailing params and the
   `mock` is typed with fewer positional args. Cleaner: type the
   mock as `vi.fn<typeof actual.fn>()` and write the wrapper as
   `((a, b, c) => mock(a, b, c)) satisfies typeof actual.fn`.
4. **`DedicatedWorkerGlobalScope` requires the `WebWorker` lib**.
   Easier to define a minimal structural `WorkerScope` interface
   in the worker file than to pull `lib: ["WebWorker"]` into
   `tsconfig` for one type reference.
5. **TS `noUncheckedIndexedAccess` + virtualizer**: when the
   template references `filteredItems[virtualItem.index]`, TS
   correctly objects with `Object is possibly undefined`. Resolve
   in the script setup by mapping `getVirtualItems()` into a typed
   `VisibleRow[]` that has already destructured the item — the
   template then just sees `row.item.id` etc.
6. **Vite's `?worker` import vs `@vueuse/core`'s `#__PURE__`
   warnings**: build emits a long string of rolldown comments
   about `@vueuse/core` annotations. Same upstream noise WU5.3
   flagged; nothing to do with the worker.
7. **`Window's scrollTo() not implemented`** stderr lines from
   `useWindowVirtualizer` under jsdom — internal scroll-position
   syncs that jsdom doesn't implement. Harmless, no failure.
8. **WU5.3's WU5.5 follow-up note** (`journal/260605-wu53-...`)
   said "manual test target: the 3.8 MB AL fixture." Did that as
   a smoke against a real 80 KB clause-shaped slice. Whole-doc
   diffing isn't a real product code path — the API hands us
   clause-level `before_text` / `after_text` — so the smoke is
   sized to the actual contract, not the doc.

## Wire contract

No change. WU5.5 is a webapp-internal performance fix.

## Follow-ups (post-demo)

1. Once WU8.2's Playwright suite is in place, add an E2E case that
   opens a clause with > 50 K combined input and asserts the
   "Computing diff…" pane appears then gets replaced. That's the
   only contract layer not currently under unit test.
2. If the demo audience asks for visible MOVED rows, the path
   lozenge with `before → after` can wrap onto two lines. The
   variable-height row sizing handles this correctly, but the
   row's `overscan: 8` might leave a brief window where the
   bottom of the visible range jumps. Consider bumping overscan
   if visible jitter appears.
3. Property-test the threshold dispatcher with fast-check
   (already captured under
   `project_horizons_post_demo_fastcheck`) — every input pair
   below threshold must hit the sync path; every pair above
   must hit the worker path.
4. The "Computing diff…" message could optionally include the
   clause size ("Computing diff for a 80 KB clause…") for visible
   reassurance on the slowest case. Bikeshed; not for the demo.
