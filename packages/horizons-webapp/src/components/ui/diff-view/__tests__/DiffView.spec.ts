import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { computeDiff, type DiffOp, type PendingDiff } from '@/lib/diff'

// vi.mock is hoisted above the test imports so DiffView's import of
// computeDiffAsync routes through the mock when the threshold is crossed.
type ComputeDiffAsyncFn = typeof import('@/lib/diff').computeDiffAsync
const computeDiffAsyncMock = vi.fn<ComputeDiffAsyncFn>()

vi.mock('@/lib/diff', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/diff')>()
  return {
    ...actual,
    computeDiffAsync: ((before, after, opts) =>
      computeDiffAsyncMock(before, after, opts)) satisfies ComputeDiffAsyncFn,
  }
})

import DiffView from '../DiffView.vue'

beforeEach(() => {
  // Default behaviour: just call the sync computeDiff and resolve immediately.
  // Tests that want to observe the pending state override this per-test.
  computeDiffAsyncMock.mockImplementation((before: string | null, after: string | null) => ({
    promise: Promise.resolve(computeDiff(before, after)),
    cancel: vi.fn<() => void>(),
  }))
})

describe('DiffView (sync path, under threshold)', () => {
  it('renders side-by-side by default with both columns', () => {
    const wrapper = mount(DiffView, {
      props: { before: 'within 6 months', after: 'within 12 months' },
    })

    const left = wrapper.get('[data-testid="diff-before"]')
    const right = wrapper.get('[data-testid="diff-after"]')
    expect(left.text()).toContain('6 months')
    expect(right.text()).toContain('12 months')
    expect(left.findAll('del').length).toBeGreaterThanOrEqual(1)
    expect(right.findAll('ins').length).toBeGreaterThanOrEqual(1)

    // No worker was solicited for a small clause.
    expect(computeDiffAsyncMock).not.toHaveBeenCalled()
  })

  it('renders unified mode as a single column with ins and del', () => {
    const wrapper = mount(DiffView, {
      props: { before: 'within 6 months', after: 'within 12 months', mode: 'unified' },
    })

    expect(wrapper.find('[data-testid="diff-before"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-after"]').exists()).toBe(false)
    const unified = wrapper.get('[data-testid="diff-unified"]')
    expect(unified.findAll('ins').length).toBeGreaterThanOrEqual(1)
    expect(unified.findAll('del').length).toBeGreaterThanOrEqual(1)
  })

  it('ADDED (before=null) renders inserts only', () => {
    const wrapper = mount(DiffView, {
      props: { before: null, after: 'a brand new clause' },
    })
    const left = wrapper.get('[data-testid="diff-before"]')
    const right = wrapper.get('[data-testid="diff-after"]')
    expect(left.text().trim()).toBe('')
    expect(right.text()).toContain('a brand new clause')
    expect(right.findAll('ins').length).toBe(1)
    expect(right.findAll('del').length).toBe(0)
  })

  it('REMOVED (after=null) renders deletes only', () => {
    const wrapper = mount(DiffView, {
      props: { before: 'a since-removed clause', after: null },
    })
    const left = wrapper.get('[data-testid="diff-before"]')
    const right = wrapper.get('[data-testid="diff-after"]')
    expect(left.text()).toContain('a since-removed clause')
    expect(left.findAll('del').length).toBe(1)
    expect(left.findAll('ins').length).toBe(0)
    expect(right.text().trim()).toBe('')
  })

  it('MOVED (identical text) renders both columns with no diff marks', () => {
    const wrapper = mount(DiffView, {
      props: { before: 'unchanged text', after: 'unchanged text' },
    })
    const left = wrapper.get('[data-testid="diff-before"]')
    const right = wrapper.get('[data-testid="diff-after"]')
    expect(left.text()).toContain('unchanged text')
    expect(right.text()).toContain('unchanged text')
    expect(wrapper.findAll('ins')).toHaveLength(0)
    expect(wrapper.findAll('del')).toHaveLength(0)
  })
})

describe('DiffView (async path, over threshold)', () => {
  it('shows the "Computing diff…" skeleton while the worker is pending', async () => {
    let resolveOps!: (ops: DiffOp[]) => void
    const promise = new Promise<DiffOp[]>((resolve) => {
      resolveOps = resolve
    })
    computeDiffAsyncMock.mockReturnValueOnce({ promise, cancel: vi.fn<() => void>() })

    const big = 'x'.repeat(60_000)
    const wrapper = mount(DiffView, { props: { before: big, after: `${big} edit` } })

    expect(wrapper.find('[data-testid="diff-computing"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="diff-before"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-after"]').exists()).toBe(false)

    resolveOps([
      { op: 0, text: big },
      { op: 1, text: ' edit' },
    ])
    await flushPromises()

    expect(wrapper.find('[data-testid="diff-computing"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-before"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="diff-after"]').exists()).toBe(true)
    expect(wrapper.findAll('ins').length).toBeGreaterThanOrEqual(1)
  })

  it('cancels the pending worker on unmount', () => {
    const cancel = vi.fn<() => void>()
    computeDiffAsyncMock.mockReturnValueOnce({
      promise: new Promise<DiffOp[]>(() => {}), // never resolves
      cancel,
    })

    const big = 'y'.repeat(60_000)
    const wrapper = mount(DiffView, { props: { before: big, after: big } })
    expect(wrapper.find('[data-testid="diff-computing"]').exists()).toBe(true)

    wrapper.unmount()
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('cancels a stale pending worker when props change to a fresh diff', async () => {
    const cancelStale = vi.fn<() => void>()
    computeDiffAsyncMock.mockReturnValueOnce({
      promise: new Promise<DiffOp[]>(() => {}), // never resolves; will be cancelled
      cancel: cancelStale,
    })

    const big1 = 'a'.repeat(60_000)
    const big2 = 'b'.repeat(60_000)
    const wrapper = mount(DiffView, { props: { before: big1, after: big1 } })

    // Second computeDiffAsync call: resolve immediately.
    computeDiffAsyncMock.mockReturnValueOnce({
      promise: Promise.resolve([{ op: 0, text: big2 }] as DiffOp[]),
      cancel: vi.fn<() => void>(),
    })

    await wrapper.setProps({ before: big2, after: big2 })
    await flushPromises()

    expect(cancelStale).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="diff-computing"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="diff-before"]').exists()).toBe(true)
  })
})
