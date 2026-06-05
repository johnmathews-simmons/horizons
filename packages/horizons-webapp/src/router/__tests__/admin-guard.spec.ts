/**
 * Admin-route guard pinning. Mirrors the production router's beforeEach
 * but with stub components so the test exercises the guard, not the views.
 *
 * Pins:
 * - Unauthenticated visit to /admin/clients → /login (no role check yet).
 * - Authenticated client (role='client', kind='access') → /  (NOT /login).
 * - Authenticated admin → admin-clients passes through.
 * - Authenticated admin currently impersonating (kind='impersonation',
 *   role='client') → /  (the support-view bearer is NOT admin-equivalent).
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  createMemoryHistory,
  createRouter,
  type Router,
  type RouteRecordRaw,
} from 'vue-router'
import { defineComponent, h } from 'vue'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/api/me'
import type { ImpersonationState } from '@/stores/auth'

const ADMIN_ME: MeResponse = {
  user_id: 'admin-id',
  email: 'admin@example.test',
  role: 'admin',
  created_at: '2026-05-01T00:00:00Z',
  subscription: { active_pairs: [], is_admin_bypass: true },
}
const CLIENT_ME: MeResponse = {
  user_id: 'client-id',
  email: 'client@example.test',
  role: 'client',
  created_at: '2026-05-01T00:00:00Z',
  subscription: { active_pairs: [], is_admin_bypass: false },
}
const SUPPORT_STATE: ImpersonationState = {
  targetUserId: 'client-id',
  targetEmail: 'client@example.test',
  originalAdminId: 'admin-id',
  originalAdminEmail: 'admin@example.test',
  originalAccessToken: 'admin-access',
  originalPrincipal: ADMIN_ME,
  enteredAt: 1,
  expiresAt: 2,
}

const Stub = defineComponent({ render: () => h('div') })
const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: Stub, meta: { public: true } },
  { path: '/', name: 'home', component: Stub, meta: { requiresAuth: true } },
  {
    path: '/admin',
    component: Stub,
    meta: { requiresAuth: true, requiresAdmin: true },
    children: [
      { path: 'clients', name: 'admin-clients', component: Stub },
      { path: 'audit', name: 'admin-audit', component: Stub },
    ],
  },
]

function makeRouter(): Router {
  const router = createRouter({ history: createMemoryHistory(), routes })
  router.beforeEach((to) => {
    const auth = useAuthStore()
    if (to.meta.requiresAuth && !auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (to.meta.requiresAdmin && !auth.isAdmin) {
      return { name: 'home' }
    }
    if (to.name === 'login' && auth.isAuthenticated) {
      return auth.isAdmin ? { name: 'admin-clients' } : { name: 'home' }
    }
    return true
  })
  return router
}

describe('admin route guard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('unauthenticated visit to /admin/clients redirects to /login', async () => {
    const router = makeRouter()
    await router.push('/admin/clients')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/admin/clients')
  })

  it('authenticated client visiting /admin/clients is sent home, not to /login', async () => {
    const router = makeRouter()
    const auth = useAuthStore()
    auth.setAccessToken('client-access')
    auth.setPrincipal(CLIENT_ME)

    await router.push('/admin/clients')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('authenticated admin reaches admin-clients', async () => {
    const router = makeRouter()
    const auth = useAuthStore()
    auth.setAccessToken('admin-access')
    auth.setPrincipal(ADMIN_ME)

    await router.push('/admin/clients')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('admin-clients')
  })

  it('admin currently impersonating cannot reach /admin/clients — sent home', async () => {
    const router = makeRouter()
    const auth = useAuthStore()
    auth.setAccessToken('imp-token')
    auth.$patch({ impersonationState: SUPPORT_STATE, kind: 'impersonation' })
    // Synthesised client principal during support view.
    auth.setPrincipal(CLIENT_ME)

    await router.push('/admin/clients')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('admin landing on /login is forwarded to /admin/clients (not /)', async () => {
    const router = makeRouter()
    const auth = useAuthStore()
    auth.setAccessToken('admin-access')
    auth.setPrincipal(ADMIN_ME)

    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('admin-clients')
  })
})
