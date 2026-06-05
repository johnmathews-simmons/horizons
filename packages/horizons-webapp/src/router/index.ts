import { createRouter, createWebHistory, type RouteRecordRaw, type Router } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/changes',
    name: 'changes',
    component: () => import('@/views/ChangesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/changes/:id',
    name: 'change-detail',
    component: () => import('@/views/ChangeDetailView.vue'),
    meta: { requiresAuth: true },
    props: true,
  },
  {
    path: '/watchlists',
    name: 'watchlists',
    component: () => import('@/views/WatchlistsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/admin',
    component: () => import('@/views/AdminLayout.vue'),
    meta: { requiresAuth: true, requiresAdmin: true },
    children: [
      {
        path: '',
        name: 'admin-dashboard',
        component: () => import('@/views/AdminDashboardView.vue'),
      },
      {
        path: 'clients',
        name: 'admin-clients',
        component: () => import('@/views/AdminClientsView.vue'),
      },
      {
        path: 'clients/:id',
        name: 'admin-client-detail',
        component: () => import('@/views/AdminClientDetailView.vue'),
        props: true,
      },
      {
        path: 'audit',
        name: 'admin-audit',
        component: () => import('@/views/AdminAuditView.vue'),
      },
    ],
  },
]

export function createAppRouter(): Router {
  const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes,
  })

  // On a cold SPA bootstrap (F5, direct URL, page.goto) the in-memory
  // access token is gone but the HttpOnly refresh cookie may still be
  // valid; try once to recover it before redirecting. Without this the
  // guard would always boot reload-from-an-authed-route users to /login.
  //
  // [[adversary class 4]] — cold-bootstrap restores the *original admin's*
  // access token via the cookie, NOT the impersonation token (which was
  // in-memory only and is now gone). A reload during support view re-enters
  // the SPA as the original admin.
  let attemptedColdRefresh = false
  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    if (!attemptedColdRefresh && !auth.isAuthenticated) {
      attemptedColdRefresh = true
      try {
        await auth.refresh()
      } catch {
        // Expected when the cookie is absent or expired; fall through.
      }
    }
    if (to.meta.requiresAuth && !auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (to.meta.requiresAdmin && !auth.isAdmin) {
      // Non-admin attempting an /admin/* route. Either a real client
      // (role='client', kind='access') or a mid-impersonation admin
      // (role='client', kind='impersonation'). Either way: home, not
      // login — they ARE authenticated, just not authorised here.
      return { name: 'home' }
    }
    if (to.name === 'login' && auth.isAuthenticated) {
      return auth.isAdmin ? { name: 'admin-clients' } : { name: 'home' }
    }
    return true
  })

  return router
}

const router = createAppRouter()
export default router
