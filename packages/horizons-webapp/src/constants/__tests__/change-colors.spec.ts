import { describe, expect, it } from 'vitest'
import { CHANGE_COLORS, type ChangeType } from '../change-colors'

describe('CHANGE_COLORS', () => {
  it('defines an entry for every ChangeType', () => {
    const types: ChangeType[] = ['ADDED', 'REMOVED', 'MODIFIED', 'MOVED']
    for (const t of types) {
      expect(CHANGE_COLORS[t]).toBeDefined()
      expect(CHANGE_COLORS[t].box).toMatch(/border-|ring-/)
      expect(CHANGE_COLORS[t].box).toMatch(/bg-/)
      expect(CHANGE_COLORS[t].pill).toMatch(/bg-/)
      expect(CHANGE_COLORS[t].pill).toMatch(/text-/)
      expect(CHANGE_COLORS[t].label).toBe(t)
    }
  })

  it('uses the spec palette (green/red/amber/blue)', () => {
    expect(CHANGE_COLORS.ADDED.box).toContain('green')
    expect(CHANGE_COLORS.REMOVED.box).toContain('red')
    expect(CHANGE_COLORS.MODIFIED.box).toContain('amber')
    expect(CHANGE_COLORS.MOVED.box).toContain('blue')
  })
})
