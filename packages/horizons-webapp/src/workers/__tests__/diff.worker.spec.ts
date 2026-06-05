import { describe, expect, it } from 'vitest'
import { handleDiffMessage } from '@/workers/diff.worker'
import { computeDiff } from '@/lib/diff'

// jsdom doesn't run real Web Workers; we test the handler in-thread instead.
// The actual Worker boundary is verified by the AL-fixture manual smoke.

describe('diff worker handler', () => {
  it('returns the same ops as computeDiff for a simple edit', () => {
    const out = handleDiffMessage({ before: 'within 6 months', after: 'within 12 months' })
    expect(out).toEqual(computeDiff('within 6 months', 'within 12 months'))
  })

  it('handles ADDED (empty before)', () => {
    const out = handleDiffMessage({ before: '', after: 'fresh clause' })
    expect(out).toEqual([{ op: 1, text: 'fresh clause' }])
  })

  it('handles REMOVED (empty after)', () => {
    const out = handleDiffMessage({ before: 'gone clause', after: '' })
    expect(out).toEqual([{ op: -1, text: 'gone clause' }])
  })

  it('returns a single equal op for identical large input', () => {
    const big = 'x'.repeat(60_000)
    const out = handleDiffMessage({ before: big, after: big })
    expect(out).toEqual([{ op: 0, text: big }])
  })
})
