import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import DocumentsListView from '../DocumentsListView.vue'
import * as docsApi from '@/api/documents'

vi.mock('@/api/documents', async (orig) => {
  const actual = (await orig()) as typeof import('@/api/documents')
  return { ...actual, listDocuments: vi.fn() }
})

function makeDoc(overrides: Partial<docsApi.DocumentItem> = {}): docsApi.DocumentItem {
  return {
    id: 'doc-1',
    jurisdiction: 'UK',
    sector: 'banking',
    lawstronaut_document_id: 'L-1',
    title: 'Employment Act',
    created_at: '2026-01-01T00:00:00Z',
    clause_count: 42,
    change_counts: { added: 2, removed: 1, modified: 3, moved: 0 },
    previous_version_at: '2025-01-01T00:00:00Z',
    current_version_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

async function mountList(routeQuery: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/documents', name: 'documents', component: DocumentsListView },
      { path: '/documents/:id', name: 'document-detail', component: { template: '<div/>' } },
    ],
  })
  await router.push({ path: '/documents', query: routeQuery })
  await router.isReady()
  const wrapper = mount(DocumentsListView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient() }]],
    },
  })
  await new Promise((r) => setTimeout(r, 0))
  return { wrapper, router }
}

describe('DocumentsListView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(docsApi.listDocuments).mockReset()
  })

  it('renders an 8-column table with name, length, 4 change counts, and 2 datetimes', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 1,
      limit: 25,
      offset: 0,
    })
    const { wrapper } = await mountList()
    const headers = wrapper.findAll('thead th').map((h) => h.text())
    expect(headers).toEqual([
      'Name',
      'Length',
      'Added',
      'Removed',
      'Modified',
      'Moved',
      'Previous version',
      'Current version',
    ])
    const cells = wrapper.find('tbody tr').findAll('td').map((c) => c.text())
    expect(cells[0]).toContain('Employment Act')
    expect(cells[1]).toBe('42')
    expect(cells[2]).toBe('2')
    expect(cells[3]).toBe('1')
    expect(cells[4]).toBe('3')
    expect(cells[5]).toBe('—')
    expect(cells[6]).toBe('2025-01-01')
    expect(cells[7]).toBe('2026-01-01')
  })

  it('renders muted dash for zero change counts and empty cells for null datetimes', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [
        makeDoc({
          change_counts: { added: 0, removed: 0, modified: 0, moved: 0 },
          previous_version_at: null,
          current_version_at: null,
        }),
      ],
      total: 1,
      limit: 25,
      offset: 0,
    })
    const { wrapper } = await mountList()
    const cells = wrapper.find('tbody tr').findAll('td').map((c) => c.text())
    expect(cells.slice(2, 6)).toEqual(['—', '—', '—', '—'])
    expect(cells[6]).toBe('')
    expect(cells[7]).toBe('')
  })

  it('disables Prev on the first page and advances offset on Next', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 60,
      limit: 25,
      offset: 0,
    })
    const { wrapper, router } = await mountList()
    expect(wrapper.find('[data-testid="page-prev"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="page-next"]').trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    expect(router.currentRoute.value.query.offset).toBe('25')
  })

  it('disables Next on the final page', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 30,
      limit: 25,
      offset: 25,
    })
    const { wrapper } = await mountList({ offset: '25' })
    expect(wrapper.find('[data-testid="page-next"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="page-prev"]').attributes('disabled')).toBeUndefined()
  })
})
