import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent, h } from 'vue'
import { server } from '@/test/server'
import AdminClientsView from '../AdminClientsView.vue'

const API = 'http://localhost:8000'

interface Client {
  user_id: string
  email: string
  role: 'client'
  created_at: string
}

function makeClients(n: number, offset = 0): Client[] {
  return Array.from({ length: n }, (_, i) => ({
    user_id: `client-${offset + i + 1}`,
    email: `c${offset + i + 1}@example.test`,
    role: 'client' as const,
    created_at: '2026-05-01T00:00:00Z',
  }))
}

function mountView(): VueWrapper {
  const router: Router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/clients', name: 'admin-clients', component: AdminClientsView },
      {
        path: '/admin/clients/:id',
        name: 'admin-client-detail',
        component: defineComponent({ render: () => h('div') }),
      },
    ],
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(AdminClientsView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
    attachTo: document.body,
  })
}

describe('AdminClientsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders the empty state when no clients exist', async () => {
    server.use(
      http.get(`${API}/v1/admin/clients`, () =>
        HttpResponse.json({ limit: 25, offset: 0, total: 0, clients: [] }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders one row per client with email and id', async () => {
    const clients = makeClients(3)
    server.use(
      http.get(`${API}/v1/admin/clients`, () =>
        HttpResponse.json({ limit: 25, offset: 0, total: 3, clients }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('[data-row-testid="client-row"]')
    expect(rows).toHaveLength(3)
    expect(rows[0]?.text()).toContain('c1@example.test')
    expect(rows[0]?.text()).toContain('client-1')
    expect(wrapper.find('[data-testid="open-client-1"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('forwards limit/offset to the API on next-page click', async () => {
    const requests: string[] = []
    server.use(
      http.get(`${API}/v1/admin/clients`, ({ request }) => {
        const url = new URL(request.url)
        const limit = Number(url.searchParams.get('limit') ?? 25)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        requests.push(`${offset}/${limit}`)
        return HttpResponse.json({
          limit,
          offset,
          total: 60,
          clients: makeClients(limit, offset),
        })
      }),
    )

    const wrapper = mountView()
    await flushPromises()
    expect(requests).toContain('0/25')

    await wrapper.find('[data-testid="clients-next"]').trigger('click')
    await flushPromises()
    expect(requests).toContain('25/25')

    const indicator = wrapper.find('[data-testid="clients-page-indicator"]').text()
    expect(indicator).toContain('Page 2')
    wrapper.unmount()
  })

  it('error response renders the error state', async () => {
    server.use(
      http.get(`${API}/v1/admin/clients`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="error-state"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
