import { describe, expect, it, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import DocumentDetailView from '../DocumentDetailView.vue'
import VersionPane from '@/components/documents/VersionPane.vue'

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

interface DiscoveryItemShape {
  id: number
  document_id: string
  document_version_id: string
  jurisdiction: string
  sector: string
  change_type: 'ADDED' | 'REMOVED' | 'MODIFIED' | 'MOVED'
  before_clause_uid: string | null
  after_clause_uid: string | null
  before_path: string | null
  after_path: string | null
  alignment_confidence: number
  detected_at: string
  effective_date: string | null
}

type DiscoverySpy = ReturnType<
  typeof vi.fn<(call: { scope: string | null; document_id: string | null; limit: string | null }) => void>
>

function mockDiscovery(items: DiscoveryItemShape[]): DiscoverySpy {
  const spy: DiscoverySpy = vi.fn<
    (call: { scope: string | null; document_id: string | null; limit: string | null }) => void
  >()
  server.use(
    http.get(`${API}/v1/discovery`, ({ request }) => {
      const url = new URL(request.url)
      spy({
        scope: url.searchParams.get('scope'),
        document_id: url.searchParams.get('document_id'),
        limit: url.searchParams.get('limit'),
      })
      return HttpResponse.json({ items, next_cursor: null, has_more: false })
    }),
  )
  return spy
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
    mockDiscovery([])

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

  it('passes ADDED/MODIFIED/MOVED to right pane and REMOVED/MODIFIED/MOVED to left pane', async () => {
    mockDocument([v1Version, v2Version])
    mockClauses('v1', [c1])
    mockClauses('v2', [c1])
    mockDiscovery([
      {
        id: 1,
        document_id: DOC_ID,
        document_version_id: v2Version.id,
        jurisdiction: 'UK',
        sector: 'BANKING',
        change_type: 'ADDED',
        before_clause_uid: null,
        after_clause_uid: 'X',
        before_path: null,
        after_path: '/added/1',
        alignment_confidence: 0.9,
        detected_at: '2026-01-02T00:00:00Z',
        effective_date: null,
      },
      {
        id: 2,
        document_id: DOC_ID,
        document_version_id: v2Version.id,
        jurisdiction: 'UK',
        sector: 'BANKING',
        change_type: 'REMOVED',
        before_clause_uid: 'Y',
        after_clause_uid: null,
        before_path: '/removed/2',
        after_path: null,
        alignment_confidence: 0.9,
        detected_at: '2026-01-02T00:00:00Z',
        effective_date: null,
      },
      {
        id: 3,
        document_id: DOC_ID,
        document_version_id: v2Version.id,
        jurisdiction: 'UK',
        sector: 'BANKING',
        change_type: 'MODIFIED',
        before_clause_uid: 'Z',
        after_clause_uid: 'Z',
        before_path: '/mod/3',
        after_path: '/mod/3',
        alignment_confidence: 0.9,
        detected_at: '2026-01-02T00:00:00Z',
        effective_date: null,
      },
      {
        id: 4,
        document_id: DOC_ID,
        document_version_id: v2Version.id,
        jurisdiction: 'UK',
        sector: 'BANKING',
        change_type: 'MOVED',
        before_clause_uid: 'W',
        after_clause_uid: 'W',
        before_path: '/moved/4',
        after_path: '/moved/4b',
        alignment_confidence: 0.9,
        detected_at: '2026-01-02T00:00:00Z',
        effective_date: null,
      },
    ])

    const router = makeRouter()
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    const panes = wrapper.findAllComponents(VersionPane)
    expect(panes).toHaveLength(2)
    expect(panes[0]!.props('changeMap')).toEqual({
      '/removed/2': 'REMOVED',
      '/mod/3': 'MODIFIED',
      '/moved/4': 'MOVED',
    })
    expect(panes[1]!.props('changeMap')).toEqual({
      '/added/1': 'ADDED',
      '/mod/3': 'MODIFIED',
      '/moved/4b': 'MOVED',
    })
    expect(wrapper.find('[data-testid="diff-legend"]').exists()).toBe(true)
  })

  it('does not fetch changes or render legend when only one version exists', async () => {
    mockDocument([v1Version])
    mockClauses('v1', [c1])
    const discoverySpy = mockDiscovery([])

    const router = makeRouter()
    await router.push(`/documents/${DOC_ID}`)
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    expect(wrapper.find('[data-testid="diff-legend"]').exists()).toBe(false)
    expect(discoverySpy).not.toHaveBeenCalled()
  })

  it('forwards ?before query param as scrollToPath to the left pane and ?after to the right pane', async () => {
    mockDocument([v1Version, v2Version])
    mockClauses('v1', [c1])
    mockClauses('v2', [c1])
    mockDiscovery([])

    const router = makeRouter()
    await router.push({
      name: 'document-detail',
      params: { id: DOC_ID },
      query: { before: 'PART_1', after: 'PART_2' },
    })
    await router.isReady()
    const wrapper = mountView(router)
    await flushPromises()

    const panes = wrapper.findAllComponents(VersionPane)
    expect(panes).toHaveLength(2)
    expect(panes[0]!.props('scrollToPath')).toBe('PART_1')
    expect(panes[1]!.props('scrollToPath')).toBe('PART_2')
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
