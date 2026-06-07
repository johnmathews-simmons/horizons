import { describe, expect, it, beforeEach } from 'vitest'
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
  clause_path: 'PART_1/SECTION_1',
  text_content: 'v-something first',
  ord: 1,
}

function makeRouter(): Router {
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

    const router = makeRouter()
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

    const router = makeRouter()
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
      { ...c1, clause_path: 'PART_1/SECTION_1A', text_content: 'v2 first' },
    ])

    const router = makeRouter()
    await router.push({
      name: 'document-detail',
      params: { id: DOC_ID },
      query: { before: 'PART_1/SECTION_1', after: 'PART_1/SECTION_1A' },
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
      { ...c1, id: 'added', clause_path: 'PART_1/SECTION_2', text_content: 'added' },
    ])

    const router = makeRouter()
    await router.push({
      name: 'document-detail',
      params: { id: DOC_ID },
      query: { after: 'PART_1/SECTION_2' },
    })
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    await wrapper.get('[data-testid="toggle-structure"]').trigger('click')
    await flushPromises()

    const cards = wrapper.findAll('[data-testid="clause-card"]')
    const highlighted = cards.filter((c) => c.attributes('data-highlight') === 'true')
    expect(highlighted).toHaveLength(1)
    expect(highlighted[0]!.attributes('data-clause-path')).toBe('PART_1/SECTION_2')
  })

  it('renders the no-versions state when the document has no versions', async () => {
    mockDocument([])
    const router = makeRouter()
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
    const router = makeRouter()
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    expect(wrapper.find('[data-testid="not-found-state"]').exists()).toBe(true)
  })
})
