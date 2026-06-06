import { describe, expect, it, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { defineComponent, h } from 'vue'
import HomeView from '../HomeView.vue'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/api/me'

const Stub = defineComponent({ render: () => h('div', 'stub') })

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/login', name: 'login', component: Stub },
      { path: '/changes', name: 'changes', component: Stub },
      { path: '/watchlists', name: 'watchlists', component: Stub },
    ],
  })
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

describe('HomeView navbar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders the logged-in principal email in the header', async () => {
    const auth = useAuthStore()
    auth.setPrincipal(principal({ email: 'alice@example.test' }))

    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    const email = wrapper.get('[data-testid="user-email"]')
    expect(email.text()).toBe('alice@example.test')
  })

  it('positions the email immediately before the Sign out button', async () => {
    const auth = useAuthStore()
    auth.setPrincipal(principal({ email: 'bob@example.test' }))

    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    const header = wrapper.get('header')
    const emailIdx = header.html().indexOf('data-testid="user-email"')
    const signOutIdx = header.html().indexOf('data-testid="sign-out"')
    expect(emailIdx).toBeGreaterThan(-1)
    expect(signOutIdx).toBeGreaterThan(-1)
    expect(emailIdx).toBeLessThan(signOutIdx)
  })

  it('omits the email element when no principal is loaded', async () => {
    // No setPrincipal — auth.principal stays null.
    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    expect(wrapper.find('[data-testid="user-email"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="sign-out"]').exists()).toBe(true)
  })
})
