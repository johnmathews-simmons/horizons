# Workers

Web Workers used to keep heavy computation off the main thread.

## `diff.worker.ts`

Runs `computeDiff(before, after)` from `@/lib/diff` and posts the resulting
`DiffOp[]` back to the caller. Used by `computeDiffAsync()` when the combined
input length crosses `DIFF_WORKER_THRESHOLD` (50 000 chars).

The message handler is exported as `handleDiffMessage(request)` so it can be
unit-tested by direct import — jsdom does not ship a `Worker` constructor,
so the actual Worker boundary is exercised at runtime (production build,
WU8.2 Playwright E2E) rather than under vitest.

The `self.onmessage` side-effect at the bottom of the file is guarded by
`typeof window === 'undefined'` to avoid clobbering a window's onmessage
handler if the module is ever imported into a non-worker scope (e.g. the
unit test).

### Threshold and lifecycle

`computeDiffAsync()` in `@/lib/diff` is the only consumer:

- Below threshold → the call returns immediately via the sync `computeDiff`.
  No worker is created. Small everyday clause edits never pay the
  postMessage tax.
- At or above threshold → a one-shot worker is spawned via Vite's
  `?worker` query import. It terminates itself on reply or error. The
  returned `PendingDiff` exposes `.cancel()`, which terminates a still-
  pending worker — `DiffView` calls it on prop change and on unmount to
  avoid orphaned compute.

### Why a worker and not just async chunks

`diff_match_patch` is a single tight loop over both inputs; there is no
natural yield point. Off-main-thread is the only way to keep the demo's
clause-detail view interactive for long-tail clauses (the 3.8 MB AL
fixture has blank-line blocks up to ~88 KB, so a clause-vs-clause diff
can easily exceed 100 KB of input).
