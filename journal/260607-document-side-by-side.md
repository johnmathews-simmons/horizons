# 260607 — Document side-by-side viewer + change-in-context

**Branch:** `worktree-document-side-by-side` (fast-forward into `main` per the
WU8.4 cadence).

- Spec: [`docs/superpowers/specs/2026-06-07-document-side-by-side-and-change-context-design.md`](../docs/superpowers/specs/2026-06-07-document-side-by-side-and-change-context-design.md)
- Plan: [`docs/superpowers/plans/2026-06-07-document-side-by-side-and-change-context.md`](../docs/superpowers/plans/2026-06-07-document-side-by-side-and-change-context.md)

## 1. What landed

Webapp-only. 12 commits. Closes the demo-day UX gaps John flagged in chat:
documents with both v1 and v2 had no visible version history (the viewer
only showed the latest), and clicking a row in Recent Changes opened a
stub-feeling clause-diff snippet (`ChangeDetailView`) instead of the
whole document with the change highlighted in place.

### 1.1 Side-by-side document viewer

`packages/horizons-webapp/src/views/DocumentDetailView.vue` decomposes
into a shell that decides single-pane vs two-pane render, plus a new
`VersionPane.vue` component that owns one column's header + clauses
query + `ClauseOverlay`:

- Single-version docs: unchanged single full-width pane.
- Multi-version docs: two equal-width `<VersionPane>`s in a
  `grid-cols-1 md:grid-cols-2` layout. Oldest on the left, newest on the
  right (sort by `effective_date ?? created_at` ascending).
- Per-pane header: `version_label · seen YYYY-MM-DD`. `seen` is
  `created_at.slice(0, 10)` — when Horizons first ingested the row.
- Each pane carries its own loading/error state; one pane failing does
  not block the other. Both panes' clause queries fire in parallel.
- `ClauseOverlay` gained a `highlightPath?: string | null` prop. When
  set, it scrolls to and highlights the matching clause card via
  `Element.prototype.scrollIntoView({ block: 'center' })` and a
  `data-highlight="true"` data attribute + amber ring/background. Works
  in both flat and structure render modes (flat mode now also emits
  `data-clause-path` on each `<pre>`). Missing path → `console.warn`,
  no throw.

### 1.2 Recent Changes → document-in-context navigation

`ChangesView.vue`'s row `<RouterLink>` was rerouted from the now-retired
`change-detail` route to `document-detail` with `?before=&after=` query
params. Null paths (ADDED/REMOVED events) are dropped from the URL via
spread-with-ternary so the URL stays clean. `DocumentDetailView` reads
those params and forwards them as `highlightPath` to the matching pane.

### 1.3 Diff stack retirement

Removed in one cleanup commit:
`ChangeDetailView.vue`, `useDifferential.ts`, the entire
`components/ui/diff-view/` directory (`DiffView.vue` + tests + index),
`lib/diff.ts` + spec, `workers/diff.worker.ts` + spec + README, the
`/changes/:id` `change-detail` route, the `DifferentialItem` interface +
`fetchDifferentialById` from `api/changes.ts`, and the orphan
`diff-match-patch` + `@types/diff-match-patch` npm dependencies.

The `/v1/differential/{id}` primitive on the API stays — it's still a
documented primitive (`docs/api/horizons-primitives.md`), just nothing
in the UI calls it any more.

### 1.4 Tests

- `ClauseOverlay.spec.ts`: +6 tests covering `data-clause-path` emission
  in both modes, `data-highlight` marking, `scrollIntoView` invocation
  (jsdom prototype patch), null-highlight no-scroll, and the
  missing-path `console.warn` branch.
- `VersionPane.spec.ts`: +4 tests for header format, isolated
  loading/error/success branches, `highlightPath` forwarding.
- `DocumentDetailView.spec.ts`: +6 tests for single-pane regression,
  two-pane oldest-left ordering (with deliberately reversed API order),
  `?before=&after=` highlight routing, ADDED-shape (only `?after=`),
  no-versions state, and 404.
- `ChangesView.spec.ts`: route table updated to register
  `document-detail`; +3 tests for MODIFIED (both params), ADDED
  (before omitted), REMOVED (after omitted).
- `login-and-scope.spec.ts` (Playwright): UK + EU click-throughs now
  assert `/documents/**` URL with `before=` + `after=` query params,
  `document-title` visible, and a `data-highlight="true"` card visible
  after toggling structure mode.

### 1.5 Fixture format normalisation

`ClauseOverlay.spec.ts` and downstream specs use the canonical
`PART_1/SECTION_1` clause-path format (matches what
`packages/horizons-ingestion/src/horizons_ingestion/seed.py` emits via
`"/".join(node.path)`) rather than the spaced `PART 1 / Section 1`
that ad-hoc fixtures had drifted toward. The e2e seed keeps its own
spaced format because the e2e click-through assertions need to match
the seed's own strings.

### 1.6 Doc rot cleanup

- `e2e/README.md`: rewrote the intro to describe the new click-through
  flow; added rows for the five new testids (`document-title`,
  `version-pane-header`, `side-by-side`, `toggle-structure`,
  `document-body`); removed the `path-display` row (deleted symbol).
- `webapp/README.md`: route inventory updated from `/changes/:id` to
  `/documents` + `/documents/:id`.
- `seed_e2e.py`: the comment block referencing the retired `diff-view`
  component is now a description of the side-by-side viewer + URL query
  param mechanism.

## 2. Open items for the demo

1. **Reseed `horizons-nonprod`.** The WU8.6 v1-staging + the three new
   synthetic v2 pairs (IE 27732019, AU 2145602, EU 31366184) are
   committed but the staging corpus is still WU8.5-shape. Run
   `scripts/reseed_aca.sh --yes` to pick them up. Without the reseed,
   the new side-by-side viewer has nothing to render side-by-side on
   the deployed staging corpus.
2. **Optional post-demo polish.** The reviewer flagged a minor coverage
   gap: `DocumentDetailView`'s ISO-date sort uses `localeCompare` on
   `effective_date ?? created_at`, which only stays chronological if
   all values share the same timezone offset. The demo corpus is
   uniform UTC `Z`, so this is inert today. If `effective_date` ever
   starts arriving with mixed offsets, swap to
   `new Date(...).getTime()` comparison.

## 3. Validation summary

- 28 webapp Vitest files, 179 tests, all pass.
- `npm run build` (vue-tsc + vite) clean.
- `npm run lint:check` (oxlint + eslint) clean.
- `uv run pre-commit run --all-files` clean.
- E2E will run against CI on push.

## 4. Commit ledger

```
2d1a68f docs+test: clean up stale references and tighten e2e for side-by-side
c025aca test(e2e): navigate from Recent Changes into side-by-side document view
bd80cb4 chore(webapp): drop orphan diff-match-patch dependencies
6fca27b refactor(webapp): retire ChangeDetailView and the diff stack
00327b0 test(webapp): assert REMOVED events drop the after query param
3964c68 feat(webapp): Recent Changes rows link to side-by-side document view
adf2724 refactor(webapp): convert isNotFound to computed for reactivity correctness
5641251 feat(webapp): document detail view renders side-by-side when >1 version
f4e1312 fix(webapp): VersionPane guards useQuery against empty versionLabel
0f0d619 feat(webapp): extract VersionPane component for per-version rendering
7abda7d test(webapp): use canonical clause_path format in ClauseOverlay tests
71b4baa feat(webapp): ClauseOverlay supports highlightPath prop with auto-scroll
58f1622 docs(plan): implementation plan for side-by-side viewer + change-in-context
4ca18bd docs(spec): document side-by-side viewer + change-in-context
```
