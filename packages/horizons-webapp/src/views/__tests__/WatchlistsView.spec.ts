import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { server } from '@/test/server'
import WatchlistsView from '../WatchlistsView.vue'
import { _resetToasts } from '@/composables/useToast'

const API = 'http://localhost:8000'
const WATCHLIST_PATH = `${API}/v1/me/watchlists`
const DOCUMENTS_PATH = `${API}/v1/documents`

interface Watchlist {
  id: string
  document_id: string
  name: string
  created_at: string
  document_title?: string | null
  document_jurisdiction?: string | null
  document_sector?: string | null
}

const SAMPLE: Watchlist[] = [
  {
    id: '01900000-0000-7000-8000-000000000001',
    document_id: '01800000-0000-7000-8000-000000000001',
    name: 'Capital Requirements Act 2024',
    created_at: '2026-06-01T10:00:00Z',
    document_title: 'Capital Requirements Act 2024',
    document_jurisdiction: 'UK',
    document_sector: 'BANKING',
  },
  {
    id: '01900000-0000-7000-8000-000000000002',
    document_id: '01800000-0000-7000-8000-000000000002',
    name: 'Liquidity Coverage Directive',
    created_at: '2026-06-02T09:30:00Z',
    document_title: 'Liquidity Coverage Directive',
    document_jurisdiction: 'EU',
    document_sector: 'BANKING',
  },
]

interface DocumentSeed {
  id: string
  jurisdiction: string
  sector: string
  title?: string
}

function documentsHandler(items: DocumentSeed[]) {
  return http.get(DOCUMENTS_PATH, () =>
    HttpResponse.json({
      items: items.map((item, i) => ({
        id: item.id,
        jurisdiction: item.jurisdiction,
        sector: item.sector,
        lawstronaut_document_id: `lid-${i + 1}`,
        title: item.title ?? `Document ${item.id}`,
        created_at: '2026-06-04T08:00:00Z',
      })),
      total: items.length,
      limit: 200,
      offset: 0,
    }),
  )
}

function mountView(): VueWrapper {
  const router: Router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/watchlists', name: 'watchlists', component: WatchlistsView },
      { path: '/changes', name: 'changes', component: { template: '<div />' } },
      { path: '/documents/:id', name: 'document-detail', component: { template: '<div />' } },
    ],
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(WatchlistsView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient }]],
    },
    attachTo: document.body,
  })
}

function inPortal<T extends Element = Element>(selector: string): T | null {
  return document.querySelector<T>(selector)
}

describe('WatchlistsView — list, remove', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _resetToasts()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
    _resetToasts()
  })

  it('shows the empty state when the API returns no rows', async () => {
    server.use(http.get(WATCHLIST_PATH, () => HttpResponse.json([])))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="watchlist-table"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders one row per watchlist with name, jurisdiction, sector, doc id, and date', async () => {
    server.use(http.get(WATCHLIST_PATH, () => HttpResponse.json(SAMPLE)))

    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('[data-row-testid="watchlist-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]?.text()).toContain('Capital Requirements Act 2024')
    expect(rows[0]?.text()).toContain('UK')
    expect(rows[0]?.text()).toContain('BANKING')
    expect(rows[0]?.text()).toContain(SAMPLE[0]!.document_id)
    expect(rows[0]?.text()).toContain('2026-06-01')
    expect(rows[1]?.text()).toContain('EU')
    wrapper.unmount()
  })

  it('falls back to document_title when the user-set name is blank', async () => {
    const blankName: Watchlist[] = [
      {
        id: '01900000-0000-7000-8000-000000000bbb',
        document_id: '01800000-0000-7000-8000-000000000bbb',
        name: '',
        created_at: '2026-06-03T08:00:00Z',
        document_title: 'Foat v Department of Work and Pensions',
        document_jurisdiction: 'UK',
        document_sector: 'BANKING',
      },
    ]
    server.use(http.get(WATCHLIST_PATH, () => HttpResponse.json(blankName)))

    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('[data-row-testid="watchlist-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.text()).toContain('Foat v Department of Work and Pensions')
    wrapper.unmount()
  })

  it('falls back to document_id when both name and document_title are blank', async () => {
    const allBlank: Watchlist[] = [
      {
        id: '01900000-0000-7000-8000-000000000ccc',
        document_id: '01800000-0000-7000-8000-000000000ccc',
        name: '',
        created_at: '2026-06-03T08:00:00Z',
      },
    ]
    server.use(http.get(WATCHLIST_PATH, () => HttpResponse.json(allBlank)))

    const wrapper = mountView()
    await flushPromises()

    const rows = wrapper.findAll('[data-row-testid="watchlist-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.text()).toContain(allBlank[0]!.document_id)
    wrapper.unmount()
  })

  it('optimistically removes a row and keeps it removed on 204', async () => {
    let watchlists = [...SAMPLE]
    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json(watchlists)),
      http.delete(`${WATCHLIST_PATH}/:id`, ({ params }) => {
        watchlists = watchlists.filter((w) => w.id !== params.id)
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('[data-row-testid="watchlist-row"]')).toHaveLength(2)

    await wrapper.find(`[data-testid="remove-${SAMPLE[0]!.id}"]`).trigger('click')
    await flushPromises()
    const confirm = inPortal<HTMLButtonElement>('[data-testid="confirm-remove"]')
    expect(confirm).not.toBeNull()
    confirm!.click()
    await flushPromises()
    await flushPromises()

    const remaining = wrapper.findAll('[data-row-testid="watchlist-row"]')
    expect(remaining).toHaveLength(1)
    expect(remaining[0]?.text()).toContain('Liquidity Coverage Directive')
    expect(inPortal('[data-testid="toast-success"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('rolls back the optimistic remove on 500 and shows an error toast', async () => {
    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json(SAMPLE)),
      http.delete(`${WATCHLIST_PATH}/:id`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )

    const wrapper = mountView()
    await flushPromises()

    await wrapper.find(`[data-testid="remove-${SAMPLE[0]!.id}"]`).trigger('click')
    await flushPromises()
    const confirm = inPortal<HTMLButtonElement>('[data-testid="confirm-remove"]')
    confirm!.click()
    await flushPromises()
    await flushPromises()

    expect(wrapper.findAll('[data-row-testid="watchlist-row"]')).toHaveLength(2)
    expect(inPortal('[data-testid="toast-error"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('shows an error banner if the list fetch fails', async () => {
    server.use(http.get(WATCHLIST_PATH, () => HttpResponse.json({ detail: 'nope' }, { status: 500 })))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="error-state"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('WatchlistsView — add dialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _resetToasts()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
    _resetToasts()
  })

  it('opens the add dialog and lists in-scope documents from /v1/documents', async () => {
    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json([])),
      documentsHandler([
        { id: 'doc-uk-finance-1', jurisdiction: 'UK', sector: 'FINANCE' },
        { id: 'doc-ie-banking-2', jurisdiction: 'IE', sector: 'BANKING' },
      ]),
    )

    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="open-add-dialog"]').trigger('click')
    await flushPromises()

    const dialog = inPortal('[data-testid="add-watchlist-dialog"]')
    expect(dialog).not.toBeNull()
    const rows = document.querySelectorAll('[data-testid="discovery-row"]')
    expect(rows.length).toBe(2)
    wrapper.unmount()
  })

  it('selects a doc, posts, closes the dialog, and the list shows it after cache invalidation', async () => {
    let watchlists: Watchlist[] = []
    let postBody: { document_id: string; name?: string } | null = null

    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json(watchlists)),
      http.post(WATCHLIST_PATH, async ({ request }) => {
        postBody = (await request.json()) as { document_id: string; name?: string }
        const created: Watchlist = {
          id: '01900000-0000-7000-8000-000000000aaa',
          document_id: postBody.document_id,
          name: postBody.name ?? 'doc-uk-finance-1',
          created_at: '2026-06-05T12:00:00Z',
        }
        watchlists = [...watchlists, created]
        return HttpResponse.json(created, { status: 201 })
      }),
      documentsHandler([
        { id: 'doc-uk-finance-1', jurisdiction: 'UK', sector: 'FINANCE' },
      ]),
    )

    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="open-add-dialog"]').trigger('click')
    await flushPromises()

    const checkbox = inPortal<HTMLInputElement>(
      '[data-testid="discovery-checkbox-doc-uk-finance-1"]',
    )
    expect(checkbox).not.toBeNull()
    checkbox!.click()
    await flushPromises()

    const confirm = inPortal<HTMLButtonElement>('[data-testid="confirm-add"]')
    expect(confirm).not.toBeNull()
    confirm!.click()
    await flushPromises()
    await flushPromises()

    expect(postBody).toStrictEqual({ document_id: 'doc-uk-finance-1' })
    expect(inPortal('[data-testid="add-watchlist-dialog"]')).toBeNull()

    const rows = wrapper.findAll('[data-row-testid="watchlist-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]?.text()).toContain('doc-uk-finance-1')
    wrapper.unmount()
  })

  it('out-of-scope guard: a document not in /v1/documents is invisible in the add dialog', async () => {
    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json([])),
      documentsHandler([
        { id: 'doc-in-scope', jurisdiction: 'UK', sector: 'FINANCE' },
      ]),
    )

    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="open-add-dialog"]').trigger('click')
    await flushPromises()

    expect(inPortal('[data-testid="discovery-checkbox-doc-in-scope"]')).not.toBeNull()
    expect(inPortal('[data-testid="discovery-checkbox-doc-out-of-scope"]')).toBeNull()
    wrapper.unmount()
  })

  it('search filters by title, jurisdiction, or sector', async () => {
    server.use(
      http.get(WATCHLIST_PATH, () => HttpResponse.json([])),
      documentsHandler([
        { id: 'uk-doc', jurisdiction: 'UK', sector: 'FINANCE', title: 'UK Finance Act 2024' },
        { id: 'ie-doc', jurisdiction: 'IE', sector: 'BANKING', title: 'IE Banking Regulation' },
      ]),
    )

    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="open-add-dialog"]').trigger('click')
    await flushPromises()

    const search = inPortal<HTMLInputElement>('[data-testid="search-input"]')
    expect(search).not.toBeNull()
    search!.value = 'banking'
    search!.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    const remaining = document.querySelectorAll('[data-testid="discovery-row"]')
    expect(remaining.length).toBe(1)
    expect(remaining[0]?.textContent).toContain('IE Banking Regulation')
    wrapper.unmount()
  })
})
