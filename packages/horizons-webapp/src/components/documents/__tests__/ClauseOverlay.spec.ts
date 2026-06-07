/**
 * ClauseOverlay renders the parser's clause-by-clause view. Two modes:
 *
 *   showStructure=false — clauses run together as continuous body text,
 *                         no anchor chips, no depth indent. This is the
 *                         "reader view."
 *   showStructure=true  — each clause is a card with the anchor chip
 *                         (clause_path) on top and depth-derived indent.
 *                         This is what makes the parser's atomic unit
 *                         visible to the demo audience.
 */
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ClauseOverlay from '../ClauseOverlay.vue'
import type { ClauseItem } from '@/api/documents'

const CLAUSES: ClauseItem[] = [
  {
    id: 'c1',
    clause_uid: 'uid-1',
    clause_path: 'PART_1',
    text_content: 'Part 1 preamble text.',
    ord: 1,
  },
  {
    id: 'c2',
    clause_uid: 'uid-2',
    clause_path: 'PART_1/SECTION_2',
    text_content: 'Section 2 body.',
    ord: 2,
  },
  {
    id: 'c3',
    clause_uid: 'uid-3',
    clause_path: 'PART_1/SECTION_2/(a)/(i)',
    text_content: 'Nested clause (a)(i).',
    ord: 3,
  },
]

describe('ClauseOverlay', () => {
  it('renders continuous text without anchor chips when structure is off', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: false },
    })
    expect(wrapper.findAll('[data-testid="clause-flat"]')).toHaveLength(CLAUSES.length)
    expect(wrapper.findAll('[data-testid="clause-card"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('Part 1 preamble text.')
    expect(wrapper.text()).toContain('Nested clause (a)(i).')
  })

  it('renders one card per clause with the anchor chip when structure is on', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: true },
    })
    const cards = wrapper.findAll('[data-testid="clause-card"]')
    expect(cards).toHaveLength(CLAUSES.length)
    const anchors = wrapper.findAll('[data-testid="clause-anchor"]').map((a) => a.text())
    expect(anchors).toEqual([
      'PART_1',
      'PART_1/SECTION_2',
      'PART_1/SECTION_2/(a)/(i)',
    ])
  })

  it('indents deeper clauses further when structure is on', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: true },
    })
    const cards = wrapper.findAll('[data-testid="clause-card"]')
    const depths = cards.map((c) => Number(c.attributes('data-depth')))
    expect(depths[0]).toBe(0) // PART_1
    expect(depths[1]).toBe(1) // PART_1/SECTION_2
    expect(depths[2]).toBe(3) // PART_1/SECTION_2/(a)/(i)
    expect(depths[0]).toBeLessThan(depths[1]!)
    expect(depths[1]).toBeLessThan(depths[2]!)
  })

  it('renders nothing when given an empty clause list', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [], showStructure: true },
    })
    expect(wrapper.findAll('[data-testid="clause-card"]')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// highlightPath prop tests
// ---------------------------------------------------------------------------

const c1: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000001',
  clause_uid: '00000000-0000-4000-8000-000000000a01',
  clause_path: 'PART_1/SECTION_1',
  text_content: 'first clause',
  ord: 1,
}

const c2: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000002',
  clause_uid: '00000000-0000-4000-8000-000000000a02',
  clause_path: 'PART_1/SECTION_2',
  text_content: 'second clause',
  ord: 2,
}

describe('ClauseOverlay', () => {
  it('emits data-clause-path on every flat-mode pre so a parent can find clauses', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: false, highlightPath: null },
    })
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    expect(flats).toHaveLength(2)
    expect(flats[0]!.attributes('data-clause-path')).toBe('PART_1/SECTION_1')
    expect(flats[1]!.attributes('data-clause-path')).toBe('PART_1/SECTION_2')
  })

  it('marks the matched clause with data-highlight="true" in structure mode', async () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: true, highlightPath: 'PART_1/SECTION_2' },
    })
    await nextTick()
    const cards = wrapper.findAll('[data-testid="clause-card"]')
    expect(cards[0]!.attributes('data-highlight')).toBeUndefined()
    expect(cards[1]!.attributes('data-highlight')).toBe('true')
  })

  it('marks the matched clause with data-highlight="true" in flat mode', async () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: false, highlightPath: 'PART_1/SECTION_1' },
    })
    await nextTick()
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    expect(flats[0]!.attributes('data-highlight')).toBe('true')
    expect(flats[1]!.attributes('data-highlight')).toBeUndefined()
  })

  it('calls scrollIntoView on the highlighted clause once it mounts', async () => {
    const scrollSpy = vi.fn()
    // jsdom does not implement scrollIntoView; patch the prototype.
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: 'PART_1/SECTION_2' },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).toHaveBeenCalledTimes(1)
      expect(scrollSpy).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' })
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('does not call scrollIntoView when highlightPath is null', async () => {
    const scrollSpy = vi.fn()
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: null },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).not.toHaveBeenCalled()
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('warns to console and renders without highlight when highlightPath does not match', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const wrapper = mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, highlightPath: 'NOPE' },
      })
      await nextTick()
      expect(wrapper.findAll('[data-highlight="true"]')).toHaveLength(0)
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('ClauseOverlay: highlightPath "NOPE" not found'),
      )
    } finally {
      warnSpy.mockRestore()
    }
  })
})
