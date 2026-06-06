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
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
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
