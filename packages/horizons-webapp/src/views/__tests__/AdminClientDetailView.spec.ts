/**
 * AdminClientDetailView tests.
 *
 * Pins:
 * - Active subscription renders one row per active scope.
 * - Adding a scope sends PATCH with `add_scopes` and clears the inputs.
 * - Removing a scope opens the confirmation dialog; submitting it sends
 *   PATCH with `remove_scopes`; the response's `watchlists_soft_hidden`
 *   count is surfaced in the success toast.
 * - [[adversary class 5]] — POST /v1/admin/impersonate 500 keeps the SPA
 *   on the detail page, does NOT navigate, does NOT enter support view.
 *   An error toast is shown.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent, h } from 'vue'
import { server } from '@/test/server'
import AdminClientDetailView from '../AdminClientDetailView.vue'
import { useAuthStore } from '@/stores/auth'
import { _resetToasts, useToast } from '@/composables/useToast'
import type { MeResponse } from '@/api/me'

const API = 'http://localhost:8000'
const CLIENT_ID = '01900000-0000-7000-8000-00000000bbbb'
const SUB_ID = '01900000-0000-7000-8000-0000000000ff'

const ADMIN_ME: MeResponse = {
  user_id: 'admin-id',
  email: 'admin@example.test',
  role: 'admin',
  created_at: '2026-05-01T00:00:00Z',
  subscription: { active_pairs: [], is_admin_bypass: true },
}

interface RenderedSub {
  id: string
  user_id: string
  valid_from: string
  valid_to: string | null
  created_at: string
  scopes: Array<{ jurisdiction: string; sector: string; valid_to: string | null }>
}

function defaultSubscription(): RenderedSub {
  return {
    id: SUB_ID,
    user_id: CLIENT_ID,
    valid_from: '2026-05-01T00:00:00Z',
    valid_to: null,
    created_at: '2026-05-01T00:00:00Z',
    scopes: [
      { jurisdiction: 'GB', sector: 'banking', valid_to: null },
      { jurisdiction: 'IE', sector: 'insurance', valid_to: null },
    ],
  }
}

function inPortal<T extends Element = Element>(selector: string): T | null {
  return document.querySelector<T>(selector)
}

function mountView() {
  const router: Router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: defineComponent({ render: () => h('div') }) },
      {
        path: '/admin/clients',
        name: 'admin-clients',
        component: defineComponent({ render: () => h('div') }),
      },
      {
        path: '/admin/clients/:id',
        name: 'admin-client-detail',
        component: AdminClientDetailView,
        props: true,
      },
    ],
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    router,
    wrapper: mount(AdminClientDetailView, {
      props: { id: CLIENT_ID },
      global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
      attachTo: document.body,
    }) as VueWrapper,
  }
}

function seedSubscriptionHandlers(initial: RenderedSub) {
  let current = initial
  const patches: Array<{ subscriptionId: string; body: unknown }> = []
  server.use(
    http.get(`${API}/v1/admin/subscriptions`, () =>
      HttpResponse.json({ user_id: CLIENT_ID, subscriptions: [current] }),
    ),
    http.get(`${API}/v1/discovery`, () =>
      HttpResponse.json({
        items: [
          {
            id: 1,
            document_id: 'doc-gb-banking-1',
            document_version_id: 'v1',
            jurisdiction: 'GB',
            sector: 'banking',
            change_type: 'MODIFIED',
            before_clause_uid: null,
            after_clause_uid: null,
            before_path: null,
            after_path: 'x',
            alignment_confidence: 0.9,
            detected_at: '2026-06-04T08:00:00Z',
            effective_date: null,
          },
        ],
      }),
    ),
    http.patch(`${API}/v1/admin/subscriptions/:subId`, async ({ params, request }) => {
      const body = (await request.json()) as {
        add_scopes?: Array<{ jurisdiction: string; sector: string }>
        remove_scopes?: Array<{ jurisdiction: string; sector: string }>
      }
      patches.push({ subscriptionId: String(params.subId), body })
      const added = (body.add_scopes ?? []).length
      const removed = (body.remove_scopes ?? []).length
      // Mutate the rendered sub so the post-mutation refetch reflects.
      const removeKeys = new Set(
        (body.remove_scopes ?? []).map((p) => `${p.jurisdiction}|${p.sector}`),
      )
      current = {
        ...current,
        scopes: [
          ...current.scopes.filter((s) => !removeKeys.has(`${s.jurisdiction}|${s.sector}`)),
          ...(body.add_scopes ?? []).map((p) => ({ ...p, valid_to: null })),
        ],
      }
      return HttpResponse.json({
        subscription: current,
        scopes_added: added,
        scopes_removed: removed,
        watchlists_soft_hidden: removed > 0 ? 3 : 0,
      })
    }),
  )
  return { patches }
}

describe('AdminClientDetailView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _resetToasts()
    document.body.innerHTML = ''
    const auth = useAuthStore()
    auth.setAccessToken('admin-access-token')
    auth.setPrincipal(ADMIN_ME)
  })

  afterEach(() => {
    document.body.innerHTML = ''
    _resetToasts()
  })

  it('renders the active scopes for the subscription', async () => {
    seedSubscriptionHandlers(defaultSubscription())
    const { wrapper } = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="scope-row-GB-banking"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="scope-row-IE-insurance"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('Add scope: sends PATCH add_scopes and clears the inputs on success', async () => {
    const { patches } = seedSubscriptionHandlers(defaultSubscription())
    const { wrapper } = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="add-scope-jurisdiction"]').setValue('FR')
    await wrapper.find('[data-testid="add-scope-sector"]').setValue('insurance')
    await wrapper.find('[data-testid="add-scope-submit"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(patches).toHaveLength(1)
    expect(patches[0]).toMatchObject({
      subscriptionId: SUB_ID,
      body: { add_scopes: [{ jurisdiction: 'FR', sector: 'insurance' }] },
    })
    expect(
      (wrapper.find('[data-testid="add-scope-jurisdiction"]').element as HTMLInputElement).value,
    ).toBe('')
    wrapper.unmount()
  })

  it('Add scope: refuses a pair already active without firing a PATCH', async () => {
    const { patches } = seedSubscriptionHandlers(defaultSubscription())
    const { wrapper } = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="add-scope-jurisdiction"]').setValue('GB')
    await wrapper.find('[data-testid="add-scope-sector"]').setValue('banking')
    await wrapper.find('[data-testid="add-scope-submit"]').trigger('click')
    await flushPromises()

    expect(patches).toHaveLength(0)
    expect(wrapper.find('[data-testid="add-scope-error"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it(
    'Remove scope: opens the confirm dialog. Cancel does NOT send PATCH; explicit ' +
      'confirm sends remove_scopes and surfaces the soft-hidden count',
    async () => {
      const { patches } = seedSubscriptionHandlers(defaultSubscription())
      const { wrapper } = mountView()
      await flushPromises()

      // Open the dialog.
      await wrapper.find('[data-testid="remove-scope-GB-banking"]').trigger('click')
      await flushPromises()

      expect(inPortal('[data-testid="scope-removal-confirm-body"]')).not.toBeNull()

      // Cancel — should NOT submit.
      inPortal<HTMLButtonElement>('[data-testid="scope-removal-cancel"]')!.click()
      await flushPromises()
      expect(patches).toHaveLength(0)

      // Re-open and confirm.
      await wrapper.find('[data-testid="remove-scope-GB-banking"]').trigger('click')
      await flushPromises()
      inPortal<HTMLButtonElement>('[data-testid="scope-removal-confirm"]')!.click()
      await flushPromises()
      await flushPromises()

      expect(patches).toHaveLength(1)
      expect(patches[0]).toMatchObject({
        subscriptionId: SUB_ID,
        body: { remove_scopes: [{ jurisdiction: 'GB', sector: 'banking' }] },
      })
      // The success toast should surface the watchlists_soft_hidden count.
      const toast = useToast()
      const success = toast.toasts.find((t) => t.variant === 'success')
      expect(success).toBeDefined()
      expect(`${success?.title} ${success?.description ?? ''}`).toContain('3')
      wrapper.unmount()
    },
  )

  it(
    '[[adversary class 5]] — POST /v1/admin/impersonate 500 keeps the SPA on the ' +
      'detail page and does NOT enter support view',
    async () => {
      seedSubscriptionHandlers(defaultSubscription())
      server.use(
        http.post(`${API}/v1/admin/impersonate`, () =>
          HttpResponse.json({ detail: 'internal' }, { status: 500 }),
        ),
      )
      const auth = useAuthStore()
      const { wrapper, router } = mountView()
      await router.push({ name: 'admin-client-detail', params: { id: CLIENT_ID } })
      await router.isReady()
      await flushPromises()

      await wrapper.find('[data-testid="enter-support-view"]').trigger('click')
      await flushPromises()
      await flushPromises()

      // Defence pin: the SPA did NOT swap into support view.
      expect(auth.isImpersonating).toBe(false)
      expect(auth.kind).toBe('access')
      expect(auth.accessToken).toBe('admin-access-token')
      // Still on the detail route.
      expect(router.currentRoute.value.name).toBe('admin-client-detail')
      // Error toast surfaced (the ToastViewport lives in AdminLayout, not
      // in this view, so we read the toast queue directly).
      const toast = useToast()
      expect(toast.toasts.some((t) => t.variant === 'error')).toBe(true)
      wrapper.unmount()
    },
  )

  it('Enter support view on 201: swaps to impersonation and navigates home', async () => {
    seedSubscriptionHandlers(defaultSubscription())
    server.use(
      http.post(`${API}/v1/admin/impersonate`, () =>
        HttpResponse.json(
          {
            impersonation_token: 'imp-token',
            target_user_id: CLIENT_ID,
            target_email: 'client@example.test',
            original_admin_id: ADMIN_ME.user_id,
            original_admin_email: ADMIN_ME.email,
            expires_in_seconds: 900,
          },
          { status: 201 },
        ),
      ),
    )
    const auth = useAuthStore()
    const { wrapper, router } = mountView()
    await router.push({ name: 'admin-client-detail', params: { id: CLIENT_ID } })
    await router.isReady()
    await flushPromises()

    await wrapper.find('[data-testid="enter-support-view"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(auth.isImpersonating).toBe(true)
    expect(auth.kind).toBe('impersonation')
    expect(auth.accessToken).toBe('imp-token')
    expect(router.currentRoute.value.name).toBe('home')
    wrapper.unmount()
  })
})
