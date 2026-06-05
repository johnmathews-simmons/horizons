import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { server } from '@/test/server'
import AdminAuditView from '../AdminAuditView.vue'

const API = 'http://localhost:8000'

interface Row {
  id: string
  admin_id: string
  target_user_id: string | null
  mode: 'operator' | 'impersonation'
  token_id: string | null
  reason: string | null
  granted_at: string
}

const OPERATOR_ROW: Row = {
  id: 'row-op-1',
  admin_id: 'admin-a',
  target_user_id: null,
  mode: 'operator',
  token_id: null,
  reason: null,
  granted_at: '2026-06-05T12:00:00Z',
}

const IMPERSONATION_ROW: Row = {
  id: 'row-imp-1',
  admin_id: 'admin-a',
  target_user_id: 'client-b',
  mode: 'impersonation',
  token_id: 'tok-1',
  reason: 'support ticket #42',
  granted_at: '2026-06-05T12:05:00Z',
}

function mountView(): VueWrapper {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(AdminAuditView, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    attachTo: document.body,
  })
}

describe('AdminAuditView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('defaults since to ~ now-24h and renders both operator + impersonation rows distinctly', async () => {
    const queries: URL[] = []
    server.use(
      http.get(`${API}/v1/admin/audit`, ({ request }) => {
        queries.push(new URL(request.url))
        return HttpResponse.json({
          since: queries[queries.length - 1]!.searchParams.get('since'),
          limit: 100,
          count: 2,
          rows: [IMPERSONATION_ROW, OPERATOR_ROW],
        })
      }),
    )

    const wrapper = mountView()
    await flushPromises()

    expect(queries).toHaveLength(1)
    const since = queries[0]!.searchParams.get('since')
    expect(since).not.toBeNull()
    // since must be a real ISO string in the past 24h.
    const sinceMs = new Date(since!).getTime()
    const now = Date.now()
    expect(now - sinceMs).toBeGreaterThan(23 * 60 * 60 * 1000)
    expect(now - sinceMs).toBeLessThan(25 * 60 * 60 * 1000)

    // Operator vs impersonation rows are visually distinct (different mode pill).
    const opPill = wrapper.find(`[data-testid="audit-mode-${OPERATOR_ROW.id}"]`)
    const impPill = wrapper.find(`[data-testid="audit-mode-${IMPERSONATION_ROW.id}"]`)
    expect(opPill.text()).toBe('Operator')
    expect(impPill.text()).toBe('Impersonation')

    // The impersonation row is the one with a non-null target user id rendered.
    const impRow = wrapper.find(`[data-testid="audit-row-${IMPERSONATION_ROW.id}"]`)
    expect(impRow.text()).toContain('client-b')
    expect(impRow.text()).toContain('support ticket #42')
    // The operator row has no target.
    const opRow = wrapper.find(`[data-testid="audit-row-${OPERATOR_ROW.id}"]`)
    expect(opRow.text()).toContain('—')
    wrapper.unmount()
  })

  it('Apply forwards admin_id / target_user_id / action / since to the API', async () => {
    const queries: URL[] = []
    server.use(
      http.get(`${API}/v1/admin/audit`, ({ request }) => {
        queries.push(new URL(request.url))
        return HttpResponse.json({
          since: queries[queries.length - 1]!.searchParams.get('since') ?? '',
          limit: 100,
          count: 0,
          rows: [],
        })
      }),
    )

    const wrapper = mountView()
    await flushPromises()
    expect(queries).toHaveLength(1)

    await wrapper.find('[data-testid="filter-admin-id"]').setValue('admin-a')
    await wrapper.find('[data-testid="filter-target-id"]').setValue('client-b')
    await wrapper.find('[data-testid="filter-action"]').setValue('impersonation')
    await wrapper.find('[data-testid="filter-apply"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(queries.length).toBeGreaterThanOrEqual(2)
    const last = queries[queries.length - 1]!
    expect(last.searchParams.get('admin_id')).toBe('admin-a')
    expect(last.searchParams.get('target_user_id')).toBe('client-b')
    expect(last.searchParams.get('action')).toBe('impersonation')
    wrapper.unmount()
  })

  it('error response renders the error state', async () => {
    server.use(
      http.get(`${API}/v1/admin/audit`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('[data-testid="error-state"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
