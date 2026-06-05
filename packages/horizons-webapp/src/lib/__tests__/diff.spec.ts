import { describe, expect, it } from 'vitest'
import { computeDiff } from '@/lib/diff'

describe('computeDiff', () => {
  it('returns a single equal op for identical strings', () => {
    const ops = computeDiff('hello world', 'hello world')
    expect(ops).toEqual([{ op: 0, text: 'hello world' }])
  })

  it('returns a single insert when before is empty', () => {
    const ops = computeDiff('', 'new clause text')
    expect(ops).toEqual([{ op: 1, text: 'new clause text' }])
  })

  it('returns a single delete when after is empty', () => {
    const ops = computeDiff('old clause text', '')
    expect(ops).toEqual([{ op: -1, text: 'old clause text' }])
  })

  it('returns mixed ops for a real edit', () => {
    const ops = computeDiff('within 6 months', 'within 12 months')
    // The diff should contain at least one delete of "6" and one insert of "12"
    expect(ops.some((o) => o.op === -1 && o.text.includes('6'))).toBe(true)
    expect(ops.some((o) => o.op === 1 && o.text.includes('12'))).toBe(true)
    // The reconstruction of the before/after sides must match the originals
    const before = ops
      .filter((o) => o.op !== 1)
      .map((o) => o.text)
      .join('')
    const after = ops
      .filter((o) => o.op !== -1)
      .map((o) => o.text)
      .join('')
    expect(before).toBe('within 6 months')
    expect(after).toBe('within 12 months')
  })

  it('coerces null before to empty string (treats as ADDED)', () => {
    const ops = computeDiff(null, 'brand new')
    expect(ops).toEqual([{ op: 1, text: 'brand new' }])
  })

  it('coerces null after to empty string (treats as REMOVED)', () => {
    const ops = computeDiff('was here', null)
    expect(ops).toEqual([{ op: -1, text: 'was here' }])
  })
})
