import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { defineComponent, h } from 'vue'
import { useAuthStore } from '@/stores/auth'

const LoginStub = defineComponent({ name: 'LoginStub', render: () => h('div', 'login') })
const HomeStub = defineComponent({ name: 'HomeStub', render: () => h('div', 'home') })

function makeGuardedRouter(): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginStub, meta: { public: true } },
      { path: '/', name: 'home', component: HomeStub, meta: { requiresAuth: true } },
    ],
  })
  router.beforeEach((to) => {
    const auth = useAuthStore()
    if (to.meta.requiresAuth && !auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (to.name === 'login' && auth.isAuthenticated) {
      return { name: 'home' }
    }
    return true
  })
  return router
}

describe('navigation guard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('unauthenticated visit to / redirects to /login with a redirect query', async () => {
    const router = makeGuardedRouter()
    await router.push('/')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('authenticated visit to / stays on home', async () => {
    const router = makeGuardedRouter()
    const auth = useAuthStore()
    auth.setAccessToken('access-1')

    await router.push('/')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('authenticated visit to /login bounces to /', async () => {
    const router = makeGuardedRouter()
    const auth = useAuthStore()
    auth.setAccessToken('access-1')

    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })
})
