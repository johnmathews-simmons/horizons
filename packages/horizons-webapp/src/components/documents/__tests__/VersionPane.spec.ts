import { describe, expect, it, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import VersionPane from '../VersionPane.vue'
import type { ChangeType } from '@/constants/change-colors'

const API = 'http://localhost:8000'

const DOC_ID = '11111111-1111-4111-8111-111111111111'
const VERSION_LABEL = 'v1'

interface PaneProps {
  documentId: string
  versionLabel: string
  seenAt: string
  showStructure: boolean
  changeMap?: Record<string, ChangeType> | null
  scrollToPath?: string | null
}

function mountPane(props: PaneProps) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return mount(VersionPane, {
    props,
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
}

describe('VersionPane', () => {
  beforeEach(() => {
    server.resetHandlers()
  })

  it('renders the per-pane header with label and ISO seen-date', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: false,
    })
    await flushPromises()

    const header = wrapper.get('[data-testid="version-pane-header"]')
    expect(header.text()).toContain('v1')
    expect(header.text()).toContain('seen 2026-05-12')
  })

  it('shows its own loading state then renders clauses', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [
            {
              id: '33333333-3333-4333-8333-333333333333',
              clause_uid: '33333333-3333-4333-8333-333333333a01',
              clause_path: 'PART_1/SECTION_1',
              text_content: 'a clause',
              ord: 1,
            },
          ],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: true,
    })

    expect(wrapper.find('[data-testid="version-pane-loading"]').exists()).toBe(true)
    await flushPromises()
    expect(wrapper.find('[data-testid="version-pane-loading"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="clause-card"]')).toHaveLength(1)
  })

  it('renders its own error state when the clauses endpoint fails', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: false,
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="version-pane-error"]').text()).toContain(
      'Could not load',
    )
  })

  it('forwards changeMap and scrollToPath to ClauseOverlay', async () => {
    server.use(
      http.get(`${API}/v1/documents/${DOC_ID}/versions/${VERSION_LABEL}/clauses`, () =>
        HttpResponse.json({
          document_id: DOC_ID,
          version_id: '22222222-2222-4222-8222-222222222222',
          version_label: VERSION_LABEL,
          clauses: [
            {
              id: '33333333-3333-4333-8333-333333333333',
              clause_uid: '33333333-3333-4333-8333-333333333a01',
              clause_path: 'PART_1/SECTION_1',
              text_content: 'a clause',
              ord: 1,
            },
          ],
        }),
      ),
    )

    const wrapper = mountPane({
      documentId: DOC_ID,
      versionLabel: VERSION_LABEL,
      seenAt: '2026-05-12T08:30:00Z',
      showStructure: true,
      changeMap: { 'PART_1/SECTION_1': 'ADDED' },
      scrollToPath: 'PART_1/SECTION_1',
    })
    await flushPromises()

    const overlay = wrapper.findComponent({ name: 'ClauseOverlay' })
    expect(overlay.exists()).toBe(true)
    expect(overlay.props('changeMap')).toEqual({ 'PART_1/SECTION_1': 'ADDED' })
    expect(overlay.props('scrollToPath')).toBe('PART_1/SECTION_1')
  })
})
