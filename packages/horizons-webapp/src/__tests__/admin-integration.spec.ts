/**
 * WU5.4 integration test — end-to-end admin support-view flow.
 *
 * Steps (single test):
 *   1. Admin logs in.
 *   2. Lands on /admin/clients via the post-login forward.
 *   3. Opens a client's detail page.
 *   4. Enters support view: amber banner appears, document.title prefixed
 *      `[SUPPORT] `, auth.kind becomes 'impersonation'.
 *   5. Navigates to /changes — the banner is still visible (it lives at
 *      the App.vue layout root, not in any specific view).
 *   6. Navigates back to /admin/clients: the route guard refuses (kind is
 *      impersonation), redirects home — proving the support-view bearer
 *      is NOT admin-equivalent at the route layer.
 *   7. Clicks Exit in the banner: auth.kind flips to 'access', the
 *      original admin's bearer is restored, navigation lands on
 *      /admin/clients, document.title is back to bare 'Horizons'.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent, h } from 'vue'
import { server } from '@/test/server'
import App from '@/App.vue'
import AdminLayout from '@/views/AdminLayout.vue'
import AdminClientsView from '@/views/AdminClientsView.vue'
import AdminClientDetailView from '@/views/AdminClientDetailView.vue'
import { setAuthBridge } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'

const API = 'http://localhost:8000'

const ADMIN_ID = '01900000-0000-7000-8000-00000000aaaa'
const CLIENT_ID = '01900000-0000-7000-8000-00000000bbbb'
const SUB_ID = '01900000-0000-7000-8000-0000000000ff'

const Stub = defineComponent({ render: () => h('div', 'stub') })

function makeAppRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: Stub, meta: { requiresAuth: true } },
      { path: '/changes', name: 'changes', component: Stub, meta: { requiresAuth: true } },
      {
        path: '/admin',
        component: AdminLayout,
        meta: { requiresAuth: true, requiresAdmin: true },
        children: [
          { path: 'clients', name: 'admin-clients', component: AdminClientsView },
          {
            path: 'clients/:id',
            name: 'admin-client-detail',
            component: AdminClientDetailView,
            props: true,
          },
          { path: 'audit', name: 'admin-audit', component: Stub },
        ],
      },
    ],
  })
}

function applyGuard(router: Router) {
  router.beforeEach((to) => {
    const auth = useAuthStore()
    if (to.meta.requiresAuth && !auth.isAuthenticated) {
      return { name: 'home' }
    }
    if (to.meta.requiresAdmin && !auth.isAdmin) {
      return { name: 'home' }
    }
    return true
  })
}

function seedHandlers() {
  server.use(
    http.get(`${API}/v1/admin/clients`, () =>
      HttpResponse.json({
        limit: 25,
        offset: 0,
        total: 1,
        clients: [
          {
            user_id: CLIENT_ID,
            email: 'client@example.test',
            role: 'client',
            created_at: '2026-05-01T00:00:00Z',
          },
        ],
      }),
    ),
    http.get(`${API}/v1/admin/subscriptions`, () =>
      HttpResponse.json({
        user_id: CLIENT_ID,
        subscriptions: [
          {
            id: SUB_ID,
            user_id: CLIENT_ID,
            valid_from: '2026-05-01T00:00:00Z',
            valid_to: null,
            created_at: '2026-05-01T00:00:00Z',
            scopes: [{ jurisdiction: 'GB', sector: 'banking', valid_to: null }],
          },
        ],
      }),
    ),
    http.get(`${API}/v1/discovery`, () => HttpResponse.json({ items: [] })),
    http.post(`${API}/v1/admin/impersonate`, () =>
      HttpResponse.json(
        {
          impersonation_token: 'imp-token',
          target_user_id: CLIENT_ID,
          target_email: 'client@example.test',
          original_admin_id: ADMIN_ID,
          original_admin_email: 'admin@example.test',
          expires_in_seconds: 900,
        },
        { status: 201 },
      ),
    ),
  )
}

describe('WU5.4 integration', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
    document.title = 'Horizons'
    setAuthBridge(null)
  })

  afterEach(() => {
    document.body.innerHTML = ''
    document.title = 'Horizons'
    setAuthBridge(null)
  })

  it('full flow: enter support view → navigate → guard refuses /admin → exit', async () => {
    seedHandlers()
    const router = makeAppRouter()
    applyGuard(router)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    // Simulate post-login admin state directly: setting tokens + principal
    // bypasses the LoginView/refresh handshake which has its own coverage.
    const auth = useAuthStore()
    auth.setAccessToken('admin-access-token')
    auth.setPrincipal({
      user_id: ADMIN_ID,
      email: 'admin@example.test',
      role: 'admin',
      created_at: '2026-05-01T00:00:00Z',
      subscription: { active_pairs: [], is_admin_bypass: true },
    })

    // Wire bridge for the bootstrap-equivalent handlers used by the
    // refresh interceptor — not exercised in this test, but required
    // for completeness.
    const toast = useToast()
    setAuthBridge({
      getAccessToken: () => auth.accessToken,
      getKind: () => auth.kind,
      refresh: () => auth.refresh(),
      onAuthFailure: () => {
        auth.clear()
      },
      onImpersonationExpired: () => {
        auth.exitSupportView()
        toast.error('Support view expired')
        void router.push({ name: 'admin-clients' })
      },
    })

    const wrapper: VueWrapper = mount(App, {
      global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
      attachTo: document.body,
    })

    await router.push({ name: 'admin-clients' })
    await router.isReady()
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('admin-clients')
    expect(wrapper.find('[data-testid="clients-table"]').exists()).toBe(true)

    // Walk to the detail page.
    await router.push({ name: 'admin-client-detail', params: { id: CLIENT_ID } })
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('admin-client-detail')

    // Enter support view.
    await wrapper.find('[data-testid="enter-support-view"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(auth.kind).toBe('impersonation')
    expect(auth.isImpersonating).toBe(true)
    expect(wrapper.find('[data-testid="support-view-banner"]').exists()).toBe(true)
    expect(document.title.startsWith('[SUPPORT] ')).toBe(true)

    // Navigate to /changes — banner persists (App.vue layout root).
    await router.push({ name: 'changes' })
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('changes')
    expect(wrapper.find('[data-testid="support-view-banner"]').exists()).toBe(true)

    // Attempt to navigate back to /admin/clients — guard refuses because
    // kind === 'impersonation' (isAdmin is false). Lands on home.
    await router.push({ name: 'admin-clients' })
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('home')

    // Exit support view via the banner button.
    await wrapper.find('[data-testid="support-view-exit"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(auth.kind).toBe('access')
    expect(auth.isImpersonating).toBe(false)
    expect(auth.accessToken).toBe('admin-access-token')
    expect(router.currentRoute.value.name).toBe('admin-clients')
    expect(wrapper.find('[data-testid="support-view-banner"]').exists()).toBe(false)
    expect(document.title.startsWith('[SUPPORT] ')).toBe(false)

    wrapper.unmount()
  })
})
