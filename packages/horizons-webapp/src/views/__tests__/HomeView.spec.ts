import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { defineComponent, h } from 'vue'
import HomeView from '../HomeView.vue'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/api/me'
import type { OverviewResponse } from '@/api/overview'

vi.mock('@/api/overview', () => ({
  fetchOverview: vi.fn<() => Promise<OverviewResponse>>().mockResolvedValue({
    is_admin: false,
    totals: {
      documents: 10,
      jurisdictions: 8,
      sectors: 5,
      subscribed_jurisdictions: 1,
      subscribed_sectors: 1,
    },
    jurisdictions: [
      { code: 'AL', document_count: 1, change_count: 0, subscribed: true },
      { code: 'IE', document_count: 1, change_count: 4, subscribed: false },
      { code: 'UK', document_count: 1, change_count: 3, subscribed: true },
    ],
    sectors: [
      { code: 'BANKING', document_count: 5, change_count: 7, subscribed: true },
      { code: 'employment', document_count: 2, change_count: 0, subscribed: false },
    ],
  }),
}))

const Stub = defineComponent({ render: () => h('div', 'stub') })

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/changes', name: 'changes', component: Stub },
  { path: '/documents', name: 'documents', component: Stub },
  { path: '/login', name: 'login', component: Stub },
  { path: '/watchlists', name: 'watchlists', component: Stub },
]

async function mountHome() {
  setActivePinia(createPinia())
  const router = createRouter({ history: createMemoryHistory(), routes })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = mount(HomeView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return { wrapper, router }
}

function principal(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    user_id: '01900000-0000-7000-8000-00000000aaaa',
    email: 'alice@example.test',
    role: 'client',
    created_at: '2026-05-01T00:00:00Z',
    subscription: { active_pairs: [], is_admin_bypass: false },
    ...overrides,
  }
}

describe('HomeView', () => {
  it('renders one jurisdiction card per code in the response', async () => {
    const { wrapper } = await mountHome()
    const cards = wrapper.findAll('[data-testid="jurisdiction-card"]')
    expect(cards).toHaveLength(3)
    const codes = cards.map((c) => c.attributes('data-code'))
    expect(codes).toEqual(['AL', 'IE', 'UK'])
  })

  it('marks not-subscribed cards as disabled', async () => {
    const { wrapper } = await mountHome()
    const ie = wrapper.find('[data-testid="jurisdiction-card"][data-code="IE"]')
    expect(ie.attributes('data-subscribed')).toBe('false')
    const uk = wrapper.find('[data-testid="jurisdiction-card"][data-code="UK"]')
    expect(uk.attributes('data-subscribed')).toBe('true')
  })

  it('clicking a subscribed jurisdiction with changes navigates to /documents', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="jurisdiction-card"][data-code="UK"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'documents', query: { jurisdiction: 'UK' } })
  })

  it('clicking a subscribed jurisdiction with 0 changes navigates to /documents', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="jurisdiction-card"][data-code="AL"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'documents', query: { jurisdiction: 'AL' } })
  })

  it('clicking a not-subscribed jurisdiction does not navigate', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="jurisdiction-card"][data-code="IE"]').trigger('click')
    expect(push).not.toHaveBeenCalled()
  })

  it('clicking a subscribed sector with changes navigates to /documents', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="sector-card"][data-code="BANKING"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'documents', query: { sector: 'BANKING' } })
  })

  it('shows the change count on each jurisdiction card', async () => {
    const { wrapper } = await mountHome()
    const uk = wrapper.find('[data-testid="jurisdiction-card"][data-code="UK"]')
    expect(uk.text()).toMatch(/3\s+recent\s+changes/)
    const al = wrapper.find('[data-testid="jurisdiction-card"][data-code="AL"]')
    expect(al.text()).toMatch(/0\s+recent\s+changes/)
  })

  it('shows the summary numbers from totals', async () => {
    const { wrapper } = await mountHome()
    const text = wrapper.text()
    expect(text).toMatch(/Jurisdictions/)
    expect(text).toMatch(/1\s*\/\s*8/)
    expect(text).toMatch(/Sectors/)
    expect(text).toMatch(/1\s*\/\s*5/)
  })
})

describe('HomeView navbar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders the logged-in principal email in the header', async () => {
    const auth = useAuthStore()
    auth.setPrincipal(principal({ email: 'alice@example.test' }))

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, {
      global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
    })

    const email = wrapper.get('[data-testid="user-email"]')
    expect(email.text()).toBe('alice@example.test')
  })

  it('positions the email immediately before the Sign out button', async () => {
    const auth = useAuthStore()
    auth.setPrincipal(principal({ email: 'bob@example.test' }))

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, {
      global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
    })

    const header = wrapper.get('header')
    const emailIdx = header.html().indexOf('data-testid="user-email"')
    const signOutIdx = header.html().indexOf('data-testid="sign-out"')
    expect(emailIdx).toBeGreaterThan(-1)
    expect(signOutIdx).toBeGreaterThan(-1)
    expect(emailIdx).toBeLessThan(signOutIdx)
  })

  it('omits the email element when no principal is loaded', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, {
      global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
    })

    expect(wrapper.find('[data-testid="user-email"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="sign-out"]').exists()).toBe(true)
  })
})
