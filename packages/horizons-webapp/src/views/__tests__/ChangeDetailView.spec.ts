import { describe, expect, it, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import ChangeDetailView from '../ChangeDetailView.vue'

const API = 'http://localhost:8000'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/changes',
        name: 'changes',
        component: { template: '<div data-testid="list-stub" />' },
      },
      { path: '/changes/:id', name: 'change-detail', component: ChangeDetailView, props: true },
    ],
  })
}

function mountDetail(router: Router) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return mount(ChangeDetailView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient }]],
    },
  })
}

const baseEvent = {
  id: 1,
  document_id: '11111111-1111-4111-8111-111111111111',
  document_version_id: '22222222-2222-4222-8222-222222222222',
  jurisdiction: 'IE',
  sector: 'BANKING',
  change_type: 'MODIFIED',
  before_clause_uid: '33333333-3333-4333-8333-333333333333',
  after_clause_uid: '33333333-3333-4333-8333-333333333333',
  before_path: 'Part 2 / Section 4 / (a)',
  after_path: 'Part 2 / Section 4 / (a)',
  alignment_confidence: 0.92,
  detected_at: '2026-06-04T12:00:00Z',
  effective_date: '2026-09-01T00:00:00Z',
  before_text: 'within 6 months',
  after_text: 'within 12 months',
}

async function pushAndMount(router: Router, id: number) {
  await router.push(`/changes/${id}`)
  await router.isReady()
  const wrapper = mountDetail(router)
  await flushPromises()
  return wrapper
}

describe('ChangeDetailView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders the MODIFIED diff side-by-side by default', async () => {
    server.use(
      http.get(`${API}/v1/differential/1`, () => HttpResponse.json(baseEvent)),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 1)

    expect(wrapper.get('[data-testid="path-display"]').text()).toContain('Part 2 / Section 4 / (a)')
    expect(wrapper.find('[data-change-type="MODIFIED"]').exists()).toBe(true)
    expect(wrapper.find('[data-confidence="high"]').exists()).toBe(true)

    expect(wrapper.get('[data-testid="diff-before"]').text()).toContain('6 months')
    expect(wrapper.get('[data-testid="diff-after"]').text()).toContain('12 months')
  })

  it('toggling to unified mode swaps the renderer', async () => {
    server.use(
      http.get(`${API}/v1/differential/1`, () => HttpResponse.json(baseEvent)),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 1)

    await wrapper.get('[data-testid="mode-unified"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="diff-before"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-after"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-unified"]').exists()).toBe(true)
  })

  it('renders an ADDED event with only the after column populated', async () => {
    server.use(
      http.get(`${API}/v1/differential/2`, () =>
        HttpResponse.json({
          ...baseEvent,
          id: 2,
          change_type: 'ADDED',
          before_clause_uid: null,
          before_path: null,
          before_text: null,
        }),
      ),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 2)

    expect(wrapper.find('[data-change-type="ADDED"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="diff-before"]').text().trim()).toBe('')
    expect(wrapper.get('[data-testid="diff-after"]').text()).toContain('within 12 months')
  })

  it('renders a REMOVED event with only the before column populated', async () => {
    server.use(
      http.get(`${API}/v1/differential/3`, () =>
        HttpResponse.json({
          ...baseEvent,
          id: 3,
          change_type: 'REMOVED',
          after_clause_uid: null,
          after_path: null,
          after_text: null,
        }),
      ),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 3)

    expect(wrapper.find('[data-change-type="REMOVED"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="diff-before"]').text()).toContain('within 6 months')
    expect(wrapper.get('[data-testid="diff-after"]').text().trim()).toBe('')
  })

  it('renders the MOVED path lozenge as before → after when paths differ', async () => {
    server.use(
      http.get(`${API}/v1/differential/4`, () =>
        HttpResponse.json({
          ...baseEvent,
          id: 4,
          change_type: 'MOVED',
          before_path: 'Part 2 / Section 4 / (a)',
          after_path: 'Part 3 / Section 1 / (b)',
          before_text: 'identical text',
          after_text: 'identical text',
        }),
      ),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 4)

    expect(wrapper.get('[data-testid="path-display"]').text()).toContain(
      'Part 2 / Section 4 / (a) → Part 3 / Section 1 / (b)',
    )
    expect(wrapper.find('[data-change-type="MOVED"]').exists()).toBe(true)
  })

  it('renders a not-found message when the API returns 404', async () => {
    server.use(
      http.get(`${API}/v1/differential/999`, () =>
        HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 999)

    expect(wrapper.get('[data-testid="not-found-state"]').text()).toContain('subscription scope')
  })

  it('renders an error message on a 500', async () => {
    server.use(
      http.get(`${API}/v1/differential/1`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )

    const router = makeRouter()
    const wrapper = await pushAndMount(router, 1)

    expect(wrapper.get('[data-testid="error-state"]').text()).toContain('Could not load')
  })
})
