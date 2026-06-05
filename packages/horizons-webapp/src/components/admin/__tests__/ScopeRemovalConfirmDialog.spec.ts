/**
 * [[adversary class 2]] — admin fat-fingers subscription scope removal.
 *
 * Defence pinned here: the modal lists every document the admin's
 * scoped-discovery feed reports as falling within the (jurisdiction,
 * sector) pairs being removed, and an unconfirmed close (Cancel, X, Esc)
 * MUST NOT emit `confirm`.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, ref, h } from 'vue'
import ScopeRemovalConfirmDialog from '../ScopeRemovalConfirmDialog.vue'
import type { AdminScopePair, DiscoveryDocumentSummary } from '@/api/admin'

const REMOVING: AdminScopePair[] = [
  { jurisdiction: 'GB', sector: 'banking' },
  { jurisdiction: 'IE', sector: 'insurance' },
]

const DOCS: DiscoveryDocumentSummary[] = [
  { document_id: 'doc-gb-banking-1', jurisdiction: 'GB', sector: 'banking' },
  { document_id: 'doc-ie-insurance-1', jurisdiction: 'IE', sector: 'insurance' },
  { document_id: 'doc-de-banking-1', jurisdiction: 'DE', sector: 'banking' },
  { document_id: 'doc-gb-banking-2', jurisdiction: 'GB', sector: 'banking' },
]

function inPortal<T extends Element = Element>(selector: string): T | null {
  return document.querySelector<T>(selector)
}

function mountHarness(opts: { open?: boolean } = {}) {
  const open = ref(opts.open ?? true)
  const confirmed = ref(0)
  const Harness = defineComponent({
    setup() {
      return () =>
        h(ScopeRemovalConfirmDialog, {
          open: open.value,
          removingScopes: REMOVING,
          documents: DOCS,
          'onUpdate:open': (v: boolean) => (open.value = v),
          onConfirm: () => (confirmed.value += 1),
        })
    },
  })
  return {
    wrapper: mount(Harness, { attachTo: document.body }),
    open,
    confirmed,
  }
}

describe('ScopeRemovalConfirmDialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('lists only documents whose (jurisdiction, sector) match a scope being removed', async () => {
    const { wrapper } = mountHarness()
    await flushPromises()

    // The data-testid on <DialogContent> is dropped by Reka's portal/teleport
    // (the fragment root drops non-prop attributes — see WU5.2 journal). Probe
    // the inner body div, which is always rendered when the dialog is open.
    expect(inPortal('[data-testid="scope-removal-confirm-body"]')).not.toBeNull()

    // Two scope pairs are being removed.
    const pairEls = document.querySelectorAll('[data-testid="scope-removal-pair"]')
    expect(pairEls).toHaveLength(2)

    // Three of the four discovery docs fall inside those scopes.
    expect(inPortal('[data-testid="scope-removal-doc-doc-gb-banking-1"]')).not.toBeNull()
    expect(inPortal('[data-testid="scope-removal-doc-doc-ie-insurance-1"]')).not.toBeNull()
    expect(inPortal('[data-testid="scope-removal-doc-doc-gb-banking-2"]')).not.toBeNull()

    // The (DE, banking) doc is OUT of scope being removed; not listed.
    expect(inPortal('[data-testid="scope-removal-doc-doc-de-banking-1"]')).toBeNull()
    wrapper.unmount()
  })

  it('Cancel closes the dialog and does NOT emit confirm', async () => {
    const { wrapper, open, confirmed } = mountHarness()
    await flushPromises()

    const cancel = inPortal<HTMLButtonElement>('[data-testid="scope-removal-cancel"]')
    expect(cancel).not.toBeNull()
    cancel!.click()
    await flushPromises()

    expect(open.value).toBe(false)
    expect(confirmed.value).toBe(0)
    wrapper.unmount()
  })

  it('explicit Remove button emits confirm exactly once', async () => {
    const { wrapper, confirmed } = mountHarness()
    await flushPromises()

    const confirm = inPortal<HTMLButtonElement>('[data-testid="scope-removal-confirm"]')
    expect(confirm).not.toBeNull()
    confirm!.click()
    await flushPromises()

    expect(confirmed.value).toBe(1)
    wrapper.unmount()
  })

  it('shows a no-docs message when no discovery item matches the removed scopes', async () => {
    const open = ref(true)
    const Harness = defineComponent({
      setup() {
        return () =>
          h(ScopeRemovalConfirmDialog, {
            open: open.value,
            removingScopes: [{ jurisdiction: 'ZZ', sector: 'nothing' }],
            documents: DOCS,
            'onUpdate:open': (v: boolean) => (open.value = v),
            onConfirm: () => undefined,
          })
      },
    })
    const wrapper = mount(Harness, { attachTo: document.body })
    await flushPromises()

    expect(inPortal('[data-testid="scope-removal-no-docs"]')).not.toBeNull()
    wrapper.unmount()
  })
})
