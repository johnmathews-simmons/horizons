/**
 * [[adversary class 3]] — banner + tab-title defence-in-depth pinned here.
 *
 * The banner is part of the layout root (NOT a route view). The CSS-edge-case
 * failure mode where the amber background fails to render is mitigated by:
 *
 * 1. The persistent tab-title prefix `[SUPPORT]` (set unconditionally via
 *    the `useSupportViewTitle` watcher).
 * 2. `role="status"` + `aria-live="polite"` so screen readers announce
 *    entry / exit.
 * 3. The banner element being focusable text + a real focusable button
 *    (not relying on background colour to convey state).
 *
 * Pins:
 * - Banner DOM presence is gated by `impersonationState !== null`.
 * - Banner carries `aria-live="polite"` and `role="status"`.
 * - The amber class (`bg-amber-500`) is present so a quick contrast audit
 *   has a stable selector.
 * - `document.title` is prefixed `[SUPPORT] ` whenever impersonating.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { defineComponent, h } from 'vue'
import SupportViewBanner from '../SupportViewBanner.vue'
import { useAuthStore } from '@/stores/auth'
import { useSupportViewTitle } from '@/composables/useSupportViewTitle'
import type { MeResponse } from '@/api/me'
import type { ImpersonationState } from '@/stores/auth'

const ADMIN: MeResponse = {
  user_id: 'admin-id',
  email: 'admin@example.test',
  role: 'admin',
  created_at: '2026-05-01T00:00:00Z',
  subscription: { active_pairs: [], is_admin_bypass: true },
}

const SUPPORT_STATE: ImpersonationState = {
  targetUserId: 'client-id',
  targetEmail: 'client@example.test',
  originalAdminId: 'admin-id',
  originalAdminEmail: 'admin@example.test',
  originalAccessToken: 'admin-access',
  originalPrincipal: ADMIN,
  enteredAt: 1_700_000_000_000,
  expiresAt: 1_700_000_900_000,
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/clients', name: 'admin-clients', component: { template: '<div />' } },
      { path: '/changes', name: 'changes', component: { template: '<div />' } },
    ],
  })
}

describe('SupportViewBanner', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders nothing when impersonationState is null', () => {
    const router = makeRouter()
    const wrapper = mount(SupportViewBanner, { global: { plugins: [router] } })
    expect(wrapper.find('[data-testid="support-view-banner"]').exists()).toBe(false)
  })

  it('renders banner with target + admin emails when impersonating', async () => {
    const router = makeRouter()
    const auth = useAuthStore()
    auth.setAccessToken('imp-token')
    // Inject the impersonation state directly so we don't need to walk
    // through the full enterSupportView flow.
    auth.$patch({ impersonationState: SUPPORT_STATE, kind: 'impersonation' })

    const wrapper = mount(SupportViewBanner, { global: { plugins: [router] } })

    const banner = wrapper.find('[data-testid="support-view-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.attributes('role')).toBe('status')
    expect(banner.attributes('aria-live')).toBe('polite')
    expect(banner.classes()).toContain('bg-amber-500')

    expect(wrapper.find('[data-testid="support-view-target"]').text()).toBe('client@example.test')
    expect(wrapper.find('[data-testid="support-view-admin"]').text()).toBe('admin@example.test')
    expect(wrapper.find('[data-testid="support-view-exit"]').exists()).toBe(true)
  })

  it('Exit button drops impersonation and navigates to /admin/clients', async () => {
    const router = makeRouter()
    await router.push('/changes')
    await router.isReady()
    const auth = useAuthStore()
    auth.setAccessToken('imp-token')
    auth.$patch({ impersonationState: SUPPORT_STATE, kind: 'impersonation' })

    const wrapper = mount(SupportViewBanner, { global: { plugins: [router] } })

    await wrapper.find('[data-testid="support-view-exit"]').trigger('click')
    await flushPromises()

    expect(auth.isImpersonating).toBe(false)
    expect(auth.kind).toBe('access')
    expect(auth.accessToken).toBe('admin-access')
    expect(router.currentRoute.value.name).toBe('admin-clients')
  })
})

describe('useSupportViewTitle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.title = 'Horizons'
  })

  afterEach(() => {
    document.title = 'Horizons'
  })

  it('prefixes document.title with [SUPPORT] while impersonating', async () => {
    const auth = useAuthStore()
    const Probe = defineComponent({
      setup() {
        useSupportViewTitle()
        return () => h('div')
      },
    })
    mount(Probe)
    await flushPromises()

    expect(document.title).toBe('Horizons')

    auth.setAccessToken('imp')
    auth.$patch({ impersonationState: SUPPORT_STATE, kind: 'impersonation' })
    await flushPromises()

    expect(document.title.startsWith('[SUPPORT] ')).toBe(true)

    auth.exitSupportView()
    await flushPromises()

    expect(document.title.startsWith('[SUPPORT] ')).toBe(false)
  })
})
