import { describe, expect, it, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import ChangesView from '../ChangesView.vue'

const API = 'http://localhost:8000'

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

function mountChanges(router: Router) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return mount(ChangesView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient }]],
    },
  })
}

const baseItem = {
  document_id: '11111111-1111-4111-8111-111111111111',
  document_version_id: '22222222-2222-4222-8222-222222222222',
  jurisdiction: 'IE',
  sector: 'BANKING',
  before_clause_uid: '33333333-3333-4333-8333-333333333333',
  after_clause_uid: '33333333-3333-4333-8333-333333333333',
  before_path: 'Part 2 / Section 4 / (a)',
  after_path: 'Part 2 / Section 4 / (a)',
  detected_at: '2026-06-04T12:00:00Z',
  effective_date: '2026-09-01T00:00:00Z',
}

describe('ChangesView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders the empty state when the API returns no events', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({ items: [], next_cursor: null, has_more: false }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    expect(wrapper.get('[data-testid="empty-state"]').text()).toContain('No recent changes')
  })

  it('renders modified/added/removed events with their pills and confidence badges', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({
          items: [
            { ...baseItem, id: 1, change_type: 'MODIFIED', alignment_confidence: 0.92 },
            { ...baseItem, id: 2, change_type: 'ADDED', alignment_confidence: 1.0 },
            { ...baseItem, id: 3, change_type: 'REMOVED', alignment_confidence: 0.7 },
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

    const rows = wrapper.findAll('[data-testid="change-row"]')
    expect(rows).toHaveLength(3)
    expect(wrapper.findAll('[data-change-type="MODIFIED"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-change-type="ADDED"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-change-type="REMOVED"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-confidence]')).toHaveLength(3)
  })

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
    const href = link.attributes('href')!
    expect(href).toContain(`/documents/${baseItem.document_id}`)
    expect(href).toContain('before=Part')
    expect(href).toContain('after=Part')

    await link.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('document-detail')
    expect(router.currentRoute.value.params.id).toBe(baseItem.document_id)
    expect(router.currentRoute.value.query.before).toContain('Part')
    expect(router.currentRoute.value.query.after).toContain('Part')
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

  it('hides MOVED events by default and shows them when the toggle is on', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({
          items: [
            { ...baseItem, id: 1, change_type: 'MODIFIED', alignment_confidence: 0.92 },
            { ...baseItem, id: 2, change_type: 'MOVED', alignment_confidence: 0.95 },
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

    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-change-type="MOVED"]')).toHaveLength(0)

    await wrapper.get('[data-testid="toggle-moved"]').setValue(true)
    await flushPromises()
    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-change-type="MOVED"]')).toHaveLength(1)
  })

  it('hides below-threshold confidences by default and shows them when toggled', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({
          items: [
            { ...baseItem, id: 1, change_type: 'MODIFIED', alignment_confidence: 0.92 },
            { ...baseItem, id: 2, change_type: 'MODIFIED', alignment_confidence: 0.5 },
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

    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-confidence="low"]')).toHaveLength(0)

    await wrapper.get('[data-testid="toggle-below-threshold"]').setValue(true)
    await flushPromises()
    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-confidence="low"]')).toHaveLength(1)
  })

  it('paginates: shows Load more, fetches the next page, appends rows', async () => {
    const cursors: (string | null)[] = []
    server.use(
      http.get(`${API}/v1/discovery`, ({ request }) => {
        const url = new URL(request.url)
        const cursor = url.searchParams.get('cursor')
        cursors.push(cursor)
        if (cursors.length === 1) {
          return HttpResponse.json({
            items: [{ ...baseItem, id: 1, change_type: 'MODIFIED', alignment_confidence: 0.92 }],
            next_cursor: 'page-2-token',
            has_more: true,
          })
        }
        return HttpResponse.json({
          items: [{ ...baseItem, id: 2, change_type: 'MODIFIED', alignment_confidence: 0.81 }],
          next_cursor: null,
          has_more: false,
        })
      }),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(1)
    await wrapper.get('[data-testid="load-more"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="change-row"]')).toHaveLength(2)
    expect(wrapper.find('[data-testid="load-more"]').exists()).toBe(false)
    expect(cursors).toEqual([null, 'page-2-token'])
  })

  it('renders an error message when the discovery request fails', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    expect(wrapper.get('[data-testid="error-state"]').text()).toContain('Could not load')
  })

  it('windows the list so a large page does not render every row at once', async () => {
    const items = Array.from({ length: 1000 }, (_, i) => ({
      ...baseItem,
      id: i + 1,
      change_type: 'MODIFIED',
      alignment_confidence: 0.9,
    }))
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({ items, next_cursor: null, has_more: false }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    // Sanity: the data came back.
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(false)
    // But far fewer rows are rendered than items returned: the virtualiser
    // is windowing. The exact rendered count depends on viewport / overscan,
    // but it must be well below the total.
    const rendered = wrapper.findAll('[data-testid="change-row"]').length
    expect(rendered).toBeGreaterThan(0)
    expect(rendered).toBeLessThan(200)
  })

  it('uses generic copy — no firm or bank names', async () => {
    server.use(
      http.get(`${API}/v1/discovery`, () =>
        HttpResponse.json({ items: [], next_cursor: null, has_more: false }),
      ),
    )

    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const wrapper = mountChanges(router)
    await flushPromises()

    const text = wrapper.text().toLowerCase()
    expect(text).not.toMatch(/barclays|hsbc|santander|natwest|jpmorgan|goldman/)
  })
})
