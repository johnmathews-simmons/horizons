import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import LoginView from '../LoginView.vue'
import HomeView from '../HomeView.vue'
import { useAuthStore } from '@/stores/auth'

const API = 'http://localhost:8000'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginView },
      { path: '/', name: 'home', component: HomeView },
    ],
  })
}

describe('LoginView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('submitting valid credentials calls the auth store and navigates home', async () => {
    server.use(
      http.post(`${API}/v1/auth/login`, () => HttpResponse.json({ access_token: 'access-1' })),
    )

    const router = makeRouter()
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, { global: { plugins: [router] } })

    await wrapper.get('[data-testid="email-input"]').setValue('alice@example.test')
    await wrapper.get('[data-testid="password-input"]').setValue('hunter2')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    const auth = useAuthStore()
    expect(auth.accessToken).toBe('access-1')
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('renders an error message on a 401 login', async () => {
    server.use(
      http.post(`${API}/v1/auth/login`, () =>
        HttpResponse.json({ detail: 'invalid credentials' }, { status: 401 }),
      ),
    )

    const router = makeRouter()
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, { global: { plugins: [router] } })

    await wrapper.get('[data-testid="email-input"]').setValue('alice@example.test')
    await wrapper.get('[data-testid="password-input"]').setValue('wrong')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    const error = wrapper.get('[data-testid="login-error"]')
    expect(error.text()).toContain('Invalid email or password')

    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('renders the generic copy (no firm names)', () => {
    const router = makeRouter()
    const wrapper = mount(LoginView, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('Sign in to Horizons')
  })

  // Defence against accidental re-use of the auth-store mock: vitest hoists
  // vi.mock above imports, but we deliberately use msw + the real store here.
  it('uses the real auth store (sanity)', () => {
    expect(vi.isMockFunction(useAuthStore)).toBe(false)
  })
})
