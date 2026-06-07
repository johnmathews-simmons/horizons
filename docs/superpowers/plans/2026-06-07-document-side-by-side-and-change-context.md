# Document side-by-side viewer + change-in-context — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Webapp-only restructure of the document detail view to render two panes when a doc has ≥2 versions (with first-seen date per pane), wire Recent Changes rows to navigate into that view auto-scrolled-and-highlighted on the changed clause, and retire `ChangeDetailView` + the now-unused diff stack.

**Architecture:** All changes live under `packages/horizons-webapp/`. No API, schema, or ingestion changes. `DocumentDetailView` decomposes into a shell that decides single-pane vs two-pane and a new `VersionPane` component that owns one version's clause query, header, and highlight. `ClauseOverlay` learns a `highlightPath` prop and emits `data-clause-path` attributes in both render modes so a parent can scroll-to-and-highlight a clause by path. `ChangesView`'s `RouterLink` target swaps from `change-detail` to `document-detail` with `?before=&after=` query params. `ChangeDetailView`, `useDifferential`, `fetchDifferentialById`, the `DiffView` component, and the now-unused `@/lib/diff` + `src/workers/diff.worker.ts` are deleted in a final cleanup task.

**Tech Stack:** Vue 3 + TypeScript + vue-router 4 + @tanstack/vue-query 5 + Tailwind. Tests: Vitest + @vue/test-utils + msw for unit specs; Playwright for e2e.

**Spec:** [`docs/superpowers/specs/2026-06-07-document-side-by-side-and-change-context-design.md`](../specs/2026-06-07-document-side-by-side-and-change-context-design.md).

---

## File structure (what each file owns)

**Create:**
- `packages/horizons-webapp/src/components/documents/VersionPane.vue` — one column of the side-by-side viewer. Owns: per-version header (`label · seen YYYY-MM-DD`), a `useQuery` against `getClauses(documentId, versionLabel)`, the per-pane loading/error state, and the `ClauseOverlay`. Forwards `highlightPath` and `showStructure` props down to `ClauseOverlay`. One pane is reused for the single-version case.
- `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts` — Vitest spec for the shell: single-version, two-version, URL-anchored highlight cases.
- `packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts` — Vitest spec for pane in isolation: header text, loading/error isolation, forwards `highlightPath`.
- `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts` — Vitest spec for the highlight + scroll behaviour (if no existing spec; create).

**Modify:**
- `packages/horizons-webapp/src/components/documents/ClauseOverlay.vue` — add `highlightPath?: string | null` prop, add `data-clause-path` attribute in flat mode (already present in structure mode), call `scrollIntoView({ block: 'center', behavior: 'auto' })` on the matched clause when `highlightPath` is set, add a `data-highlight="true"` attribute + tinted ring/background classes on the matched clause.
- `packages/horizons-webapp/src/views/DocumentDetailView.vue` — shell only after refactor: fetches the document, sorts versions, decides single-pane vs two-pane render, reads `before` + `after` query params and forwards them as `highlightPath` to the correct panes. The version-fetch + ClauseOverlay code moves into `VersionPane.vue`.
- `packages/horizons-webapp/src/views/ChangesView.vue` — change the `<RouterLink :to>` target on line 213 from `change-detail` to `document-detail` with `before` + `after` in the query.
- `packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts` — update the route table and add an assertion on the new click target.
- `packages/horizons-webapp/src/router/index.ts` — remove the `change-detail` route block (lines 23-29 in current file).
- `packages/horizons-webapp/src/api/changes.ts` — drop `DifferentialItem` interface + `fetchDifferentialById` function.
- `packages/horizons-webapp/e2e/login-and-scope.spec.ts` — update the two click-through assertions (lines 87-93 and 121-127) to navigate to `/documents/*` and assert the highlighted clause is visible in the side-by-side viewer.

**Delete:**
- `packages/horizons-webapp/src/views/ChangeDetailView.vue`
- `packages/horizons-webapp/src/views/__tests__/ChangeDetailView.spec.ts`
- `packages/horizons-webapp/src/composables/useDifferential.ts`
- `packages/horizons-webapp/src/components/ui/diff-view/` (whole directory: `DiffView.vue`, `index.ts`, `__tests__/DiffView.spec.ts`)
- `packages/horizons-webapp/src/lib/diff.ts` (only consumer was `DiffView`)
- `packages/horizons-webapp/src/lib/__tests__/diff.spec.ts` if present (verify in Task 5)
- `packages/horizons-webapp/src/workers/diff.worker.ts` (only consumer was `DiffView` via `@/lib/diff`)
- `packages/horizons-webapp/src/workers/__tests__/diff.worker.spec.ts` if present (verify in Task 5)
- `packages/horizons-webapp/src/workers/README.md` (file's entire purpose was documenting the diff worker — verify in Task 5 it documents only `diff.worker.ts`; if any other worker exists, edit instead of delete)

---

## Task 1: `ClauseOverlay` learns to highlight a clause by path

**Files:**
- Test: `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts` (create)
- Modify: `packages/horizons-webapp/src/components/documents/ClauseOverlay.vue`

- [ ] **Step 1: Write the failing test**

Create `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ClauseOverlay from '../ClauseOverlay.vue'
import type { ClauseItem } from '@/api/documents'

const c1: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000001',
  clause_uid: '00000000-0000-4000-8000-000000000a01',
  clause_path: 'PART 1 / Section 1',
  text_content: 'first clause',
  ord: 1,
}

const c2: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000002',
  clause_uid: '00000000-0000-4000-8000-000000000a02',
  clause_path: 'PART 1 / Section 2',
  text_content: 'second clause',
  ord: 2,
}

describe('ClauseOverlay', () => {
  it('emits data-clause-path on every flat-mode pre so a parent can find clauses', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: false, highlightPath: null },
    })
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    expect(flats).toHaveLength(2)
    expect(flats[0]!.attributes('data-clause-path')).toBe('PART 1 / Section 1')
    expect(flats[1]!.attributes('data-clause-path')).toBe('PART 1 / Section 2')
  })

  it('marks the matched clause with data-highlight="true" in structure mode', async () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: true, highlightPath: 'PART 1 / Section 2' },
    })
    await nextTick()
    const cards = wrapper.findAll('[data-testid="clause-card"]')
    expect(cards[0]!.attributes('data-highlight')).toBeUndefined()
    expect(cards[1]!.attributes('data-highlight')).toBe('true')
  })

  it('marks the matched clause with data-highlight="true" in flat mode', async () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: false, highlightPath: 'PART 1 / Section 1' },
    })
    await nextTick()
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    expect(flats[0]!.attributes('data-highlight')).toBe('true')
    expect(flats[1]!.attributes('data-highlight')).toBeUndefined()
  })

  it('calls scrollIntoView on the highlighted clause once it mounts', async () => {
    const scrollSpy = vi.fn()
    // jsdom does not implement scrollIntoView; patch the prototype.
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: 'PART 1 / Section 2' },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).toHaveBeenCalledTimes(1)
      expect(scrollSpy).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' })
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('does not call scrollIntoView when highlightPath is null', async () => {
    const scrollSpy = vi.fn()
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: null },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).not.toHaveBeenCalled()
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('warns to console and renders without highlight when highlightPath does not match', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const wrapper = mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: 'NOPE' },
      })
      await nextTick()
      expect(wrapper.findAll('[data-highlight="true"]')).toHaveLength(0)
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('ClauseOverlay: highlightPath "NOPE" not found'),
      )
    } finally {
      warnSpy.mockRestore()
    }
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/ClauseOverlay.spec.ts`
Expected: tests fail — `highlightPath` prop unknown, no `data-clause-path` on flat pre, no `data-highlight` attribute.

- [ ] **Step 3: Modify `ClauseOverlay.vue`**

Replace the entire file with:

```vue
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ClauseItem } from '@/api/documents'

interface Props {
  clauses: ClauseItem[]
  showStructure: boolean
  highlightPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { highlightPath: null })

interface DepthClause {
  clause: ClauseItem
  depth: number
}

const decorated = computed<DepthClause[]>(() =>
  props.clauses.map((c) => ({
    clause: c,
    depth: Math.max(0, c.clause_path.split('/').length - 1),
  })),
)

const root = ref<HTMLElement | null>(null)

function scrollToHighlight(): void {
  const target = props.highlightPath
  if (!target) return
  if (!root.value) return
  const match = props.clauses.find((c) => c.clause_path === target)
  if (!match) {
    console.warn(`ClauseOverlay: highlightPath "${target}" not found in clauses`)
    return
  }
  // Look up the rendered element by its data attribute. Works for both
  // flat and structure modes — both emit data-clause-path.
  const safe = CSS.escape(target)
  const el = root.value.querySelector(`[data-clause-path="${safe}"]`)
  if (!(el instanceof HTMLElement)) return
  el.scrollIntoView({ block: 'center', behavior: 'auto' })
}

onMounted(() => {
  void nextTick().then(() => scrollToHighlight())
})

watch(
  () => [props.highlightPath, props.clauses.length] as const,
  () => {
    void nextTick().then(() => scrollToHighlight())
  },
)

function isHighlighted(path: string): boolean {
  return props.highlightPath !== null && props.highlightPath === path
}
</script>

<template>
  <div ref="root" data-testid="document-body" class="bg-white p-6 shadow-sm">
    <!-- Continuous mode: clauses run together, parser boundaries invisible. -->
    <template v-if="!showStructure">
      <article class="prose prose-slate max-w-none">
        <pre
          v-for="dc in decorated"
          :key="dc.clause.id"
          data-testid="clause-flat"
          :data-clause-path="dc.clause.clause_path"
          :data-highlight="isHighlighted(dc.clause.clause_path) ? 'true' : undefined"
          class="mb-4 whitespace-pre-wrap font-serif text-base text-slate-800"
          :class="
            isHighlighted(dc.clause.clause_path)
              ? 'rounded-md bg-amber-100 ring-2 ring-amber-400 p-3'
              : ''
          "
        >{{ dc.clause.text_content }}</pre>
      </article>
    </template>

    <!-- Structure mode: parser's clause boundaries visible as cards with -->
    <!-- anchor chips. Depth comes from the clause_path segment count. -->
    <template v-else>
      <ol class="space-y-3" data-testid="clause-cards">
        <li
          v-for="dc in decorated"
          :key="dc.clause.id"
          data-testid="clause-card"
          :data-clause-path="dc.clause.clause_path"
          :data-depth="dc.depth"
          :data-highlight="isHighlighted(dc.clause.clause_path) ? 'true' : undefined"
          class="rounded-md border border-slate-200 bg-slate-50"
          :class="
            isHighlighted(dc.clause.clause_path)
              ? 'ring-2 ring-amber-400 bg-amber-50'
              : ''
          "
          :style="{ marginLeft: `${dc.depth * 1.25}rem` }"
        >
          <div
            class="flex items-center justify-between border-b border-slate-200 px-3 py-1.5"
          >
            <code
              data-testid="clause-anchor"
              class="rounded bg-slate-200 px-1.5 py-0.5 text-xs font-medium text-slate-700"
            >
              {{ dc.clause.clause_path }}
            </code>
            <span class="text-xs text-slate-400">#{{ dc.clause.ord }}</span>
          </div>
          <pre
            class="whitespace-pre-wrap px-3 py-3 font-serif text-sm text-slate-800"
          >{{ dc.clause.text_content }}</pre>
        </li>
      </ol>
    </template>
  </div>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/ClauseOverlay.spec.ts`
Expected: all 6 tests pass.

Also run the existing `DocumentDetailView.vue` consumers haven't broken:

Run: `cd packages/horizons-webapp && npx vitest run`
Expected: all existing specs still pass (the new prop has a default of `null`, so existing call sites are unaffected).

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/components/documents/ClauseOverlay.vue \
        packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts
git commit -m "feat(webapp): ClauseOverlay supports highlightPath prop with auto-scroll"
```

---

## Task 2: Extract `VersionPane` component

**Files:**
- Create: `packages/horizons-webapp/src/components/documents/VersionPane.vue`
- Test: `packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts`:

```ts
import { describe, expect, it, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import VersionPane from '../VersionPane.vue'

const API = 'http://localhost:8000'

const DOC_ID = '11111111-1111-4111-8111-111111111111'
const VERSION_LABEL = 'v1'

function mountPane(props: Record<string, unknown>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return mount(VersionPane, {
    props,
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
}

describe('VersionPane', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  it('renders the per-pane header with label and ISO seen-date', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: false,
      highlightPath: null,
    })
    await flushPromises()

    const header = wrapper.get('[data-testid="version-pane-header"]')
    expect(header.text()).toContain('v1')
    expect(header.text()).toContain('seen 2026-05-12')
  })

  it('shows its own loading state then renders clauses', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [
            {
              id: '33333333-3333-4333-8333-333333333333',
              clause_uid: '33333333-3333-4333-8333-333333333a01',
              clause_path: 'PART 1 / Section 1',
              text_content: 'a clause',
              ord: 1,
            },
          ],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: true,
      highlightPath: null,
    })

    expect(wrapper.find('[data-testid="version-pane-loading"]').exists()).toBe(true)
    await flushPromises()
    expect(wrapper.find('[data-testid="version-pane-loading"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="clause-card"]')).toHaveLength(1)
  })

  it('renders its own error state when the clauses endpoint fails', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: false,
      highlightPath: null,
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="version-pane-error"]').text()).toContain(
      'Could not load',
    )
  })

  it('forwards highlightPath to ClauseOverlay', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [
            {
              id: '33333333-3333-4333-8333-333333333333',
              clause_uid: '33333333-3333-4333-8333-333333333a01',
              clause_path: 'PART 1 / Section 1',
              text_content: 'a clause',
              ord: 1,
            },
          ],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: true,
      highlightPath: 'PART 1 / Section 1',
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="clause-card"]').attributes('data-highlight')).toBe('true')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/VersionPane.spec.ts`
Expected: FAIL — `VersionPane.vue` does not exist.

- [ ] **Step 3: Create `VersionPane.vue`**

Create `packages/horizons-webapp/src/components/documents/VersionPane.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { getClauses, type ClauseBundle } from '@/api/documents'
import ClauseOverlay from './ClauseOverlay.vue'

interface Props {
  documentId: string
  versionLabel: string
  seenAt: string
  showStructure: boolean
  highlightPath: string | null
}

const props = defineProps<Props>()

const query = useQuery<ClauseBundle>({
  queryKey: computed(() => ['document-clauses', props.documentId, props.versionLabel]),
  queryFn: () => getClauses(props.documentId, props.versionLabel),
})

const seenDate = computed<string>(() => props.seenAt.slice(0, 10))
</script>

<template>
  <section class="flex min-w-0 flex-col">
    <header
      data-testid="version-pane-header"
      class="mb-2 flex items-baseline gap-2 border-b border-slate-200 pb-2"
    >
      <span class="text-sm font-semibold text-slate-900">{{ versionLabel }}</span>
      <span class="text-xs text-slate-500">· seen {{ seenDate }}</span>
    </header>

    <div
      v-if="query.isPending.value"
      data-testid="version-pane-loading"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      Loading clauses…
    </div>

    <div
      v-else-if="query.isError.value"
      role="alert"
      data-testid="version-pane-error"
      class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
    >
      Could not load the clauses for this version.
    </div>

    <ClauseOverlay
      v-else-if="query.data.value"
      :clauses="query.data.value.clauses"
      :show-structure="showStructure"
      :highlight-path="highlightPath"
    />
  </section>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/VersionPane.spec.ts`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/components/documents/VersionPane.vue \
        packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts
git commit -m "feat(webapp): extract VersionPane component for per-version rendering"
```

---

## Task 3: `DocumentDetailView` renders single-pane or two-pane based on versions count

**Files:**
- Modify: `packages/horizons-webapp/src/views/DocumentDetailView.vue`
- Test: `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts`:

```ts
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import DocumentDetailView from '../DocumentDetailView.vue'

const API = 'http://localhost:8000'
const DOC_ID = '11111111-1111-4111-8111-111111111111'

const v1Version = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  version_label: 'v1',
  publication_date: null,
  effective_date: null,
  content_bytes: 100,
  created_at: '2026-05-12T08:30:00Z',
}
const v2Version = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  version_label: 'v2',
  publication_date: null,
  effective_date: null,
  content_bytes: 100,
  created_at: '2026-06-04T09:00:00Z',
}

const c1 = {
  id: '33333333-3333-4333-8333-333333333001',
  clause_uid: '33333333-3333-4333-8333-333333333a01',
  clause_path: 'PART 1 / Section 1',
  text_content: 'v-something first',
  ord: 1,
}

function makeRouter(initialPath: string): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      {
        path: '/documents/:id',
        name: 'document-detail',
        component: DocumentDetailView,
        props: true,
      },
    ],
  })
}

function mountView(router: Router) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return mount(DocumentDetailView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
}

function mockDocument(versions: typeof v1Version[]): void {
  server.use(
    http.get(`${API}/v1/documents/${DOC_ID}`, () =>
      HttpResponse.json({
        id: DOC_ID,
        jurisdiction: 'UK',
        sector: 'BANKING',
        lawstronaut_document_id: '27732019',
        title: 'Test Act',
        created_at: '2026-05-01T00:00:00Z',
        versions,
      }),
    ),
  )
}

function mockClauses(versionLabel: string, clauses: typeof c1[]): void {
  server.use(
    http.get(`${API}/v1/documents/${DOC_ID}/versions/${versionLabel}/clauses`, () =>
      HttpResponse.json({
        document_id: DOC_ID,
        version_id: versionLabel === 'v1' ? v1Version.id : v2Version.id,
        version_label: versionLabel,
        clauses,
      }),
    ),
  )
}

describe('DocumentDetailView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    server.resetHandlers()
  })

  it('renders a single VersionPane when the document has one version', async () => {
    mockDocument([v1Version])
    mockClauses('v1', [c1])

    const router = makeRouter('/')
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    const headers = wrapper.findAll('[data-testid="version-pane-header"]')
    expect(headers).toHaveLength(1)
    expect(headers[0]!.text()).toContain('v1')
    expect(headers[0]!.text()).toContain('seen 2026-05-12')
  })

  it('renders two panes oldest-left when the document has two versions', async () => {
    mockDocument([v2Version, v1Version]) // API may return any order
    mockClauses('v1', [c1])
    mockClauses('v2', [c1])

    const router = makeRouter('/')
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    const headers = wrapper.findAll('[data-testid="version-pane-header"]')
    expect(headers).toHaveLength(2)
    expect(headers[0]!.text()).toContain('v1')
    expect(headers[0]!.text()).toContain('seen 2026-05-12')
    expect(headers[1]!.text()).toContain('v2')
    expect(headers[1]!.text()).toContain('seen 2026-06-04')
  })

  it('forwards ?before and ?after query params as highlightPath to the correct panes', async () => {
    mockDocument([v1Version, v2Version])
    mockClauses('v1', [c1])
    mockClauses('v2', [
      { ...c1, clause_path: 'PART 1 / Section 1A', text_content: 'v2 first' },
    ])

    const router = makeRouter('/')
    await router.push({
      name: 'document-detail',
      params: { id: DOC_ID },
      query: { before: 'PART 1 / Section 1', after: 'PART 1 / Section 1A' },
    })
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    // Toggle into structure mode so highlight attributes land on clause-cards.
    await wrapper.get('[data-testid="toggle-structure"]').trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('[data-testid="clause-card"]')
    // Two panes × 1 card each = 2 cards. Both are the highlighted clause.
    const highlighted = cards.filter((c) => c.attributes('data-highlight') === 'true')
    expect(highlighted).toHaveLength(2)
  })

  it('with only ?after (ADDED-shape) highlights only the right pane', async () => {
    mockDocument([v1Version, v2Version])
    mockClauses('v1', [c1])
    mockClauses('v2', [
      c1,
      { ...c1, id: 'added', clause_path: 'PART 1 / Section 2', text_content: 'added' },
    ])

    const router = makeRouter('/')
    await router.push({
      name: 'document-detail',
      params: { id: DOC_ID },
      query: { after: 'PART 1 / Section 2' },
    })
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    await wrapper.get('[data-testid="toggle-structure"]').trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('[data-testid="clause-card"]')
    const highlighted = cards.filter((c) => c.attributes('data-highlight') === 'true')
    expect(highlighted).toHaveLength(1)
    expect(highlighted[0]!.attributes('data-clause-path')).toBe('PART 1 / Section 2')
  })

  it('renders the no-versions state when the document has no versions', async () => {
    mockDocument([])
    const router = makeRouter('/')
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    expect(wrapper.find('[data-testid="no-versions-state"]').exists()).toBe(true)
  })

  it('renders the not-found state on 404', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}`, () =>
        HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    )
    const router = makeRouter('/')
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    expect(wrapper.find('[data-testid="not-found-state"]').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts`
Expected: tests fail — the new VersionPane header isn't reachable from `DocumentDetailView` yet.

- [ ] **Step 3: Replace `DocumentDetailView.vue`**

Replace the entire file `packages/horizons-webapp/src/views/DocumentDetailView.vue` with:

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { getDocument, type DocumentDetail, type DocumentVersion } from '@/api/documents'
import { Button } from '@/components/ui/button'
import VersionPane from '@/components/documents/VersionPane.vue'
import AppNavBar from '@/components/AppNavBar.vue'

const route = useRoute()

const documentId = computed(() => String(route.params.id))

const docQuery = useQuery<DocumentDetail>({
  queryKey: computed(() => ['document-detail', documentId.value]),
  queryFn: () => getDocument(documentId.value),
})

const document = computed<DocumentDetail | null>(() => docQuery.data.value ?? null)

const sortedVersions = computed<DocumentVersion[]>(() => {
  const versions = document.value?.versions ?? []
  return [...versions].sort((a, b) => {
    const ad = a.effective_date ?? a.created_at
    const bd = b.effective_date ?? b.created_at
    return ad.localeCompare(bd)
  })
})

const beforePath = computed<string | null>(() => {
  const v = route.query.before
  return typeof v === 'string' && v.length > 0 ? v : null
})

const afterPath = computed<string | null>(() => {
  const v = route.query.after
  return typeof v === 'string' && v.length > 0 ? v : null
})

const showStructure = ref(false)

function isNotFound(): boolean {
  const err = docQuery.error.value as { response?: { status?: number } } | null
  return err?.response?.status === 404
}

// Single-pane case: which version do we show? Latest.
const lonePaneVersion = computed<DocumentVersion | null>(() => {
  const versions = sortedVersions.value
  if (versions.length === 0) return null
  return versions[versions.length - 1]!
})

// Highlight for the single-pane case: whichever of before/after is set.
// after takes precedence (matches the conceptual "current state").
const lonePaneHighlight = computed<string | null>(
  () => afterPath.value ?? beforePath.value,
)
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <AppNavBar />

    <section class="mx-auto max-w-7xl px-6 py-10">
      <div
        v-if="docQuery.isPending.value"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading document…
      </div>

      <div
        v-else-if="docQuery.isError.value && isNotFound()"
        data-testid="not-found-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-700"
      >
        This document isn't in your subscription scope.
      </div>

      <div
        v-else-if="docQuery.isError.value"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load the document. Please try again.
      </div>

      <template v-else-if="document">
        <div class="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 data-testid="document-title" class="text-2xl font-semibold text-slate-900">
              {{ document.title }}
            </h1>
            <div class="mt-1 text-sm text-slate-500">
              {{ document.jurisdiction }} · {{ document.sector }}
            </div>
          </div>
          <Button
            v-if="sortedVersions.length > 0"
            variant="outline"
            size="sm"
            data-testid="toggle-structure"
            :aria-pressed="showStructure"
            @click="showStructure = !showStructure"
          >
            {{ showStructure ? 'Hide clause structure' : 'Show clause structure' }}
          </Button>
        </div>

        <div
          v-if="sortedVersions.length === 0"
          data-testid="no-versions-state"
          class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-600"
        >
          No content has been ingested for this document yet. The Horizons
          worker fetches and aligns clauses on its next scheduled poll.
        </div>

        <!-- Single-version: one full-width pane. -->
        <div v-else-if="sortedVersions.length === 1 && lonePaneVersion" class="grid grid-cols-1">
          <VersionPane
            :document-id="documentId"
            :version-label="lonePaneVersion.version_label"
            :seen-at="lonePaneVersion.created_at"
            :show-structure="showStructure"
            :highlight-path="lonePaneHighlight"
          />
        </div>

        <!-- Multi-version: two equal-width panes side-by-side, oldest left. -->
        <div
          v-else
          data-testid="side-by-side"
          class="grid grid-cols-1 gap-6 md:grid-cols-2"
        >
          <VersionPane
            :document-id="documentId"
            :version-label="sortedVersions[0]!.version_label"
            :seen-at="sortedVersions[0]!.created_at"
            :show-structure="showStructure"
            :highlight-path="beforePath"
          />
          <VersionPane
            :document-id="documentId"
            :version-label="sortedVersions[sortedVersions.length - 1]!.version_label"
            :seen-at="sortedVersions[sortedVersions.length - 1]!.created_at"
            :show-structure="showStructure"
            :highlight-path="afterPath"
          />
        </div>
      </template>
    </section>
  </main>
</template>
```

Note: the view defaults to `showStructure: false` for parity with today's behaviour. The two highlight-case tests in Step 1 already toggle structure mode on via `[data-testid="toggle-structure"]` before asserting on `[data-testid="clause-card"]` — that flow exercises the realistic demo path (user clicks a row → lands on doc → toggles structure on to see clause boundaries).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts`
Expected: all 6 tests pass.

Also run the full vitest suite to confirm no regressions:

Run: `cd packages/horizons-webapp && npx vitest run`
Expected: all pre-existing specs still pass (ChangesView.spec is unchanged at this point and still asserts the old `change-detail` route — fixed in Task 4).

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/views/DocumentDetailView.vue \
        packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(webapp): document detail view renders side-by-side when >1 version"
```

---

## Task 4: ChangesView click target → document-detail with `before`/`after` query

**Files:**
- Modify: `packages/horizons-webapp/src/views/ChangesView.vue`
- Modify: `packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts`

- [ ] **Step 1: Update the test**

Edit `packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts`:

Replace the `makeRouter` function (lines 12-25) with:

```ts
function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/changes', name: 'changes', component: ChangesView },
      {
        path: '/documents/:id',
        name: 'document-detail',
        component: { template: '<div data-testid="document-detail-stub">doc</div>' },
        props: true,
      },
    ],
  })
}
```

Then add a new test case to the `describe('ChangesView', ...)` block (after the existing "renders modified/added/removed" test):

```ts
  it('clicking a row navigates to the document-detail route with before+after query', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({
          items: [
            {
              ...baseItem,
              id: 1,
              change_type: 'MODIFIED',
              alignment_confidence: 0.92,
              before_path: 'Part 2 / Section 4 / (a)',
              after_path: 'Part 2 / Section 4 / (a)',
            },
          ],
          next_cursor: null,
          has_more: false,
        }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    const link = wrapper.get('[data-testid="change-row"] a')
    expect(link.attributes('href')).toContain(`/documents/${baseItem.document_id}`)
    expect(link.attributes('href')).toContain('before=Part')
    expect(link.attributes('href')).toContain('after=Part')

    await link.trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="document-detail-stub"]').exists()).toBe(true)
  })

  it('on ADDED events (no before_path), the link omits the before query param', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({
          items: [
            {
              ...baseItem,
              id: 2,
              change_type: 'ADDED',
              alignment_confidence: 1.0,
              before_path: null,
              after_path: 'Part 2 / Section 5',
            },
          ],
          next_cursor: null,
          has_more: false,
        }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    const href = wrapper.get('[data-testid="change-row"] a').attributes('href')!
    expect(href).not.toContain('before=')
    expect(href).toContain('after=Part')
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/horizons-webapp && npx vitest run src/views/__tests__/ChangesView.spec.ts`
Expected: the two new tests fail (still routes to `change-detail`). Some pre-existing tests may also fail because the route table changed and the `change-detail` route no longer exists — the link still tries to resolve it. That's also expected: those failures get cleared by Step 3.

- [ ] **Step 3: Edit `ChangesView.vue`**

In `packages/horizons-webapp/src/views/ChangesView.vue`, replace the `<RouterLink>` block (currently lines 212-227). Find:

```vue
          <RouterLink
            :to="{ name: 'change-detail', params: { id: String(row.item.id) } }"
            class="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50"
          >
```

Replace with:

```vue
          <RouterLink
            :to="{
              name: 'document-detail',
              params: { id: row.item.document_id },
              query: {
                ...(row.item.before_path ? { before: row.item.before_path } : {}),
                ...(row.item.after_path ? { after: row.item.after_path } : {}),
              },
            }"
            class="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50"
          >
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd packages/horizons-webapp && npx vitest run src/views/__tests__/ChangesView.spec.ts`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/views/ChangesView.vue \
        packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts
git commit -m "feat(webapp): Recent Changes rows link to side-by-side document view"
```

---

## Task 5: Retire `ChangeDetailView`, `useDifferential`, `DiffView`, and the diff worker

**Files:**
- Delete: `packages/horizons-webapp/src/views/ChangeDetailView.vue`
- Delete: `packages/horizons-webapp/src/views/__tests__/ChangeDetailView.spec.ts`
- Delete: `packages/horizons-webapp/src/composables/useDifferential.ts`
- Delete: `packages/horizons-webapp/src/components/ui/diff-view/` (directory)
- Delete: `packages/horizons-webapp/src/lib/diff.ts`
- Delete: `packages/horizons-webapp/src/lib/__tests__/diff.spec.ts` (if present)
- Delete: `packages/horizons-webapp/src/workers/diff.worker.ts`
- Delete: `packages/horizons-webapp/src/workers/__tests__/` (whole directory if it only contains diff-worker tests)
- Delete: `packages/horizons-webapp/src/workers/README.md`
- Delete: `packages/horizons-webapp/src/workers/` (whole directory, if `diff.worker.ts` was the only worker)
- Modify: `packages/horizons-webapp/src/api/changes.ts`
- Modify: `packages/horizons-webapp/src/router/index.ts`

- [ ] **Step 1: Confirm scope of deletions**

Before deleting anything, confirm what depends on what:

Run: `cd packages/horizons-webapp && grep -rn "@/lib/diff\|/lib/diff\|diff.worker\|DiffView\|fetchDifferentialById\|useDifferential\|ChangeDetailView\|change-detail" src 2>/dev/null | grep -v __tests__`

Expected output: matches only in the files listed above (plus their imports of each other). If anything else turns up, stop and add it to the deletion list.

Run: `ls packages/horizons-webapp/src/workers/`

If only `diff.worker.ts` + `README.md` + `__tests__/` are present, the whole `workers/` directory can be deleted. If any other worker file exists, keep the directory and edit `README.md` instead.

Run: `ls packages/horizons-webapp/src/lib/`

If only `diff.ts` (+ tests) and `utils.ts` are present, delete just `diff.ts` and its test. Keep `utils.ts`.

- [ ] **Step 2: Delete the dead files**

```bash
git rm packages/horizons-webapp/src/views/ChangeDetailView.vue
git rm packages/horizons-webapp/src/views/__tests__/ChangeDetailView.spec.ts
git rm packages/horizons-webapp/src/composables/useDifferential.ts
git rm -r packages/horizons-webapp/src/components/ui/diff-view
git rm packages/horizons-webapp/src/lib/diff.ts
# If present:
git rm packages/horizons-webapp/src/lib/__tests__/diff.spec.ts 2>/dev/null || true
# Workers — drop the whole dir if diff.worker.ts is the only worker:
git rm -r packages/horizons-webapp/src/workers
```

- [ ] **Step 3: Edit `packages/horizons-webapp/src/api/changes.ts`**

Replace the entire file with:

```ts
import { apiClient } from './client'
import type { ChangeType } from '@/components/ui/change-type-pill'

export interface DiscoveryItem {
  id: number
  document_id: string
  document_version_id: string
  jurisdiction: string
  sector: string
  change_type: ChangeType
  before_clause_uid: string | null
  after_clause_uid: string | null
  before_path: string | null
  after_path: string | null
  alignment_confidence: number
  detected_at: string
  effective_date: string | null
}

export interface DiscoveryPage {
  items: DiscoveryItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface DiscoveryParams {
  cursor?: string | null
  limit?: number
  jurisdiction?: string | null
  sector?: string | null
}

export async function fetchDiscovery(params: DiscoveryParams = {}): Promise<DiscoveryPage> {
  const search: Record<string, string | number> = { scope: 'corpus' }
  if (params.limit !== undefined) search.limit = params.limit
  if (params.cursor) search.cursor = params.cursor
  if (params.jurisdiction) search.jurisdiction = params.jurisdiction
  if (params.sector) search.sector = params.sector
  const response = await apiClient.get<DiscoveryPage>('/v1/discovery', { params: search })
  return response.data
}
```

(`DifferentialItem` and `fetchDifferentialById` removed.)

- [ ] **Step 4: Edit `packages/horizons-webapp/src/router/index.ts`**

Remove the `change-detail` route block. Find:

```ts
  {
    path: '/changes/:id',
    name: 'change-detail',
    component: () => import('@/views/ChangeDetailView.vue'),
    meta: { requiresAuth: true },
    props: true,
  },
```

Delete those six lines.

- [ ] **Step 5: Run typecheck and tests**

Run: `cd packages/horizons-webapp && npm run build`
Expected: vue-tsc + vite build pass with no missing-import errors.

Run: `cd packages/horizons-webapp && npx vitest run`
Expected: all surviving specs pass.

Run: `cd packages/horizons-webapp && npm run lint:check`
Expected: clean.

If any of these fail with a missing-symbol error referencing one of the deleted files, search for the stale import and remove it.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-webapp/src/api/changes.ts \
        packages/horizons-webapp/src/router/index.ts
git commit -m "refactor(webapp): retire ChangeDetailView and the diff stack"
```

---

## Task 6: Update Playwright e2e for the new navigation

**Files:**
- Modify: `packages/horizons-webapp/e2e/login-and-scope.spec.ts`

- [ ] **Step 1: Update the UK assertions**

In `packages/horizons-webapp/e2e/login-and-scope.spec.ts`, replace section 3 of the first test (lines 87-93). Find:

```ts
  // -------- 3. UK clause diff --------
  await ukRow.click()
  await page.waitForURL('**/changes/*')
  await expect(page.getByTestId('path-display')).toContainText(UK_PATH)
  await expect(page.locator('[data-confidence="high"]')).toHaveText('0.92')
  await expect(page.locator('body')).toContainText(UK_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(UK_AFTER_FRAGMENT)
```

Replace with:

```ts
  // -------- 3. UK clause in document context --------
  await ukRow.click()
  await page.waitForURL('**/documents/**')
  // Document title visible (regardless of single- or two-pane layout).
  await expect(page.getByTestId('document-title')).toBeVisible()
  // Both before and after clause text are visible somewhere on the page
  // (e2e seed creates one v1 with the before text and one v2 with the
  // after text — both render in the side-by-side viewer).
  await expect(page.locator('body')).toContainText(UK_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(UK_AFTER_FRAGMENT)
```

- [ ] **Step 2: Update the EU assertions**

Find the corresponding EU block (lines 121-127):

```ts
  // -------- 7. EU clause diff --------
  await euRow.click()
  await page.waitForURL('**/changes/*')
  await expect(page.getByTestId('path-display')).toContainText(EU_PATH)
  await expect(page.locator('[data-confidence="medium"]')).toHaveText('0.78')
  await expect(page.locator('body')).toContainText(EU_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(EU_AFTER_FRAGMENT)
```

Replace with:

```ts
  // -------- 7. EU clause in document context --------
  await euRow.click()
  await page.waitForURL('**/documents/**')
  await expect(page.getByTestId('document-title')).toBeVisible()
  await expect(page.locator('body')).toContainText(EU_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(EU_AFTER_FRAGMENT)
```

- [ ] **Step 3: Update the JSDoc comment**

The top-of-file JSDoc (lines 1-32) describes the old `/changes/:id` flow. Update points 2 and 5 of the assertion list. Find:

```ts
 * 2. Clicking the UK row lands on /changes/:id with the before/after text
 *    rendered in the diff view and a green "0.92" badge.
```

Replace with:

```ts
 * 2. Clicking the UK row lands on /documents/:id (the side-by-side
 *    viewer) with the before and after clause text both visible.
```

Find:

```ts
 * 5. Clicking the EU row shows the amber "0.78" badge.
```

Replace with:

```ts
 * 5. Clicking the EU row lands on /documents/:id with the EU before
 *    and after clause text both visible.
```

- [ ] **Step 4: Note about local execution**

E2E tests need a running stack (Postgres + API + webapp) — see `packages/horizons-webapp/e2e/README.md`. Running them locally before commit is **optional**. CI runs them on push via `.github/workflows/e2e.yml`. The unit suite + build + lint check from Task 5 are the practical pre-push gates.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/e2e/login-and-scope.spec.ts
git commit -m "test(e2e): navigate from Recent Changes into side-by-side document view"
```

---

## Task 7: Local sweep + push

- [ ] **Step 1: Run the full webapp sweep**

```bash
cd packages/horizons-webapp
npm run lint:check
npm run build
npm run test:unit -- --run
```

Expected: all three commands exit 0.

- [ ] **Step 2: Run Python sweep for completeness**

No Python files changed, but run the pre-commit hook over the whole tree to catch any drift:

```bash
cd /Users/john/projects/syncthing/agent-lxc/horizons
uv run pre-commit run --all-files
```

Expected: clean. If any hook auto-fixes files, `git add` them and amend or add a follow-up commit:

```bash
git add -u
git commit -m "chore: pre-commit autofixes"
```

- [ ] **Step 3: Push the feature branch (early CI signal)**

```bash
git push -u origin <feature-branch-name>
```

This triggers `webapp.yml` (lint:check, build, vitest) and `e2e.yml` on the SHA. Per the CLAUDE.md merge cadence, status checks are non-gating but useful as a verification signal.

- [ ] **Step 4: Fast-forward into main**

From the main checkout:

```bash
git -C /Users/john/projects/syncthing/agent-lxc/horizons merge --ff-only <feature-branch-name>
git -C /Users/john/projects/syncthing/agent-lxc/horizons push origin main
git push origin --delete <feature-branch-name>
```

Then `ExitWorktree(action="remove")` to drop the worktree.

- [ ] **Step 5: Write a journal entry**

Add `journal/260607-document-side-by-side.md` (or the next date if the work spans into 2026-06-08) summarising:

- What landed (side-by-side viewer + change-context highlight + retirements).
- The diff stack deletion (`DiffView`, `@/lib/diff`, `diff.worker.ts`).
- Demo prep: reseed reminder (`scripts/reseed_aca.sh --yes` to pick up WU8.6 corpus + the IE/AU/EU synthetic v2 pairs that surface in this UI).

Commit:

```bash
git add journal/260607-document-side-by-side.md
git commit -m "docs(journal): side-by-side viewer + change-in-context landed"
git push origin main
```

---

## Self-review notes

**Spec coverage:**
- ✓ Side-by-side when >1 version: Task 3 (`DocumentDetailView` shell) + Task 2 (`VersionPane`).
- ✓ Per-pane first-seen date: Task 2 (`seenAt` prop, header renders `YYYY-MM-DD`).
- ✓ Pane order oldest→newest: Task 3 (`sortedVersions` sort + array index `[0]` vs `[length-1]`).
- ✓ URL contract `?before=&after=`: Task 3 (route reads, forwards to panes).
- ✓ Auto-scroll on mount via `scrollIntoView({block:'center'})`: Task 1.
- ✓ Persistent highlight (no auto-dismiss): Task 1 (no timeout in `scrollToHighlight`).
- ✓ Missing path → console.warn, no error: Task 1 (`console.warn` branch tested).
- ✓ Recent Changes rows route to document-detail: Task 4.
- ✓ ADDED/REMOVED query params dropped when null: Task 4 (`...(row.item.before_path ? ... : {})`).
- ✓ Retire ChangeDetailView + useDifferential + DiffView + diff lib + worker: Task 5.
- ✓ E2E updates: Task 6.

**Placeholder scan:** No TBDs. Every code block is concrete; every command shows expected output. Step 3a of Task 3 fixes a known issue inline.

**Type consistency:** `highlightPath: string | null` used consistently (ClauseOverlay, VersionPane, DocumentDetailView). `seenAt: string` (ISO) → `seenDate: string` (YYYY-MM-DD). Route query params typed as `string | undefined` and narrowed.

**No spec gap.** The deletion of `@/lib/diff` + `src/workers/diff.worker.ts` is broader than what the spec explicitly lists, but the spec's intent (retire the diff stack now that nothing consumes it) covers it. Task 5 Step 1 confirms with a grep before deleting.
