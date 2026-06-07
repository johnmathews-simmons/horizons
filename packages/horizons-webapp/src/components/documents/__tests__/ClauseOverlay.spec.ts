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
    heading_text: null,
    numbering_label: null,
    ord: 1,
  },
  {
    id: 'c2',
    clause_uid: 'uid-2',
    clause_path: 'PART_1/SECTION_2',
    text_content: 'Section 2 body.',
    heading_text: null,
    numbering_label: null,
    ord: 2,
  },
  {
    id: 'c3',
    clause_uid: 'uid-3',
    clause_path: 'PART_1/SECTION_2/(a)/(i)',
    text_content: 'Nested clause (a)(i).',
    heading_text: null,
    numbering_label: null,
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
// scrollToPath + changeMap prop tests
// ---------------------------------------------------------------------------

const c1: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000001',
  clause_uid: '00000000-0000-4000-8000-000000000a01',
  clause_path: 'PART_1/SECTION_1',
  text_content: 'first clause',
  heading_text: null,
  numbering_label: null,
  ord: 1,
}

const c2: ClauseItem = {
  id: '00000000-0000-4000-8000-000000000002',
  clause_uid: '00000000-0000-4000-8000-000000000a02',
  clause_path: 'PART_1/SECTION_2',
  text_content: 'second clause',
  heading_text: null,
  numbering_label: null,
  ord: 2,
}

describe('ClauseOverlay — scrollToPath', () => {
  it('emits data-clause-path on every flat-mode pre so a parent can find clauses', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c1, c2], showStructure: false, scrollToPath: null },
    })
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    expect(flats).toHaveLength(2)
    expect(flats[0]!.attributes('data-clause-path')).toBe('PART_1/SECTION_1')
    expect(flats[1]!.attributes('data-clause-path')).toBe('PART_1/SECTION_2')
  })

  it('does nothing when scrollToPath is null', async () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: false, scrollToPath: null },
    })
    await nextTick()
    expect(wrapper.findAll('[data-clause-path]').length).toBeGreaterThan(0)
  })

  it('calls scrollIntoView on the targeted clause once it mounts', async () => {
    const scrollSpy = vi.fn<() => void>()
    // jsdom does not implement scrollIntoView; patch the prototype.
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, scrollToPath: 'PART_1/SECTION_2' },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).toHaveBeenCalledTimes(1)
      expect(scrollSpy).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' })
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('does not call scrollIntoView when scrollToPath is null', async () => {
    const scrollSpy = vi.fn<() => void>()
    const original = (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView
    ;(Element.prototype as unknown as { scrollIntoView: typeof scrollSpy }).scrollIntoView = scrollSpy
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, scrollToPath: null },
        attachTo: document.body,
      })
      await nextTick()
      expect(scrollSpy).not.toHaveBeenCalled()
    } finally {
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = original
    }
  })

  it('warns to console when scrollToPath does not match', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      mount(ClauseOverlay, {
        props: { clauses: [c1, c2], showStructure: true, scrollToPath: 'NOPE' },
      })
      await nextTick()
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('ClauseOverlay: scrollToPath "NOPE" not found'),
      )
    } finally {
      warnSpy.mockRestore()
    }
  })
})

describe('ClauseOverlay with changeMap', () => {
  const clauses: ClauseItem[] = [
    { id: '1', clause_uid: 'a', clause_path: '/p/1', text_content: 'one', heading_text: null, numbering_label: null, ord: 0 },
    { id: '2', clause_uid: 'b', clause_path: '/p/2', text_content: 'two', heading_text: null, numbering_label: null, ord: 1 },
    { id: '3', clause_uid: 'c', clause_path: '/p/3', text_content: 'three', heading_text: null, numbering_label: null, ord: 2 },
  ]

  it('applies the ADDED box class to clauses whose path matches', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses, showStructure: false, changeMap: { '/p/2': 'ADDED' } },
    })
    const row = wrapper.find('[data-clause-path="/p/2"]')
    expect(row.classes().join(' ')).toContain('ring-green-400')
    const pill = row.find('[data-testid="clause-change-pill"]')
    expect(pill.exists()).toBe(true)
    expect(pill.text()).toBe('ADDED')
    expect(pill.classes().join(' ')).toContain('text-green-800')
  })

  it('leaves unmatched clauses without a colour box', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses, showStructure: false, changeMap: { '/p/2': 'MODIFIED' } },
    })
    const row1 = wrapper.find('[data-clause-path="/p/1"]')
    expect(row1.find('[data-testid="clause-change-pill"]').exists()).toBe(false)
    expect(row1.classes().join(' ')).not.toContain('ring-amber-400')
  })

  it('uses the right colour for each non-ADDED ChangeType', () => {
    const cases = [
      { type: 'REMOVED' as const, ring: 'ring-red-400' },
      { type: 'MODIFIED' as const, ring: 'ring-amber-400' },
      { type: 'MOVED' as const, ring: 'ring-blue-400' },
    ]
    for (const { type, ring } of cases) {
      const wrapper = mount(ClauseOverlay, {
        props: { clauses, showStructure: false, changeMap: { '/p/1': type } },
      })
      const row = wrapper.find('[data-clause-path="/p/1"]')
      expect(row.classes().join(' ')).toContain(ring)
    }
  })

  it('works in structure mode as well as flat mode', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses, showStructure: true, changeMap: { '/p/2': 'ADDED' } },
    })
    const card = wrapper.find('[data-clause-path="/p/2"]')
    expect(card.classes().join(' ')).toContain('ring-green-400')
  })
})

// ---------------------------------------------------------------------------
// heading_text rendering
// ---------------------------------------------------------------------------

const HEADED_CLAUSES: ClauseItem[] = [
  {
    id: 'h1',
    clause_uid: 'uid-h1',
    clause_path: 'what-to-expect',
    text_content: '',
    heading_text: 'What to expect',
    numbering_label: null,
    ord: 1,
  },
  {
    id: 'h2',
    clause_uid: 'uid-h2',
    clause_path: 'what-to-expect/#1',
    text_content: 'The Chair will present the assessment.',
    heading_text: null,
    numbering_label: null,
    ord: 2,
  },
  {
    id: 'h3',
    clause_uid: 'uid-h3',
    clause_path: 'what-to-expect/sub-topic',
    text_content: 'Body for the nested heading.',
    heading_text: 'Sub topic',
    numbering_label: null,
    ord: 3,
  },
]

describe('ClauseOverlay — heading_text', () => {
  it('renders heading_text as a semantic heading in flat mode', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: HEADED_CLAUSES, showStructure: false },
    })
    const headings = wrapper.findAll('[data-testid="clause-heading"]')
    expect(headings).toHaveLength(2)
    expect(headings[0]!.text()).toBe('What to expect')
    expect(headings[1]!.text()).toBe('Sub topic')
  })

  it('renders heading_text inside the structure-mode card', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: HEADED_CLAUSES, showStructure: true },
    })
    const cards = wrapper.findAll('[data-testid="clause-card"]')
    expect(cards).toHaveLength(HEADED_CLAUSES.length)
    expect(cards[0]!.find('[data-testid="clause-heading"]').text()).toBe('What to expect')
    expect(cards[2]!.find('[data-testid="clause-heading"]').text()).toBe('Sub topic')
    expect(cards[1]!.find('[data-testid="clause-heading"]').exists()).toBe(false)
  })

  it('omits the body element when text_content is empty (heading-only clause)', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: HEADED_CLAUSES, showStructure: false },
    })
    const flats = wrapper.findAll('[data-testid="clause-flat"]')
    // First clause is heading-only; its container should not include a <pre>.
    expect(flats[0]!.find('pre').exists()).toBe(false)
    expect(flats[1]!.find('pre').exists()).toBe(true)
  })

  it('picks heading level h2 at depth 0 and h3 one segment deeper', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: HEADED_CLAUSES, showStructure: false },
    })
    const headings = wrapper.findAll('[data-testid="clause-heading"]')
    // depth 0 ("what-to-expect")
    expect(headings[0]!.attributes('data-heading-level')).toBe('h2')
    expect(headings[0]!.element.tagName.toLowerCase()).toBe('h2')
    // depth 1 ("what-to-expect/sub-topic")
    expect(headings[1]!.attributes('data-heading-level')).toBe('h3')
    expect(headings[1]!.element.tagName.toLowerCase()).toBe('h3')
  })

  it('caps the heading level at h6 for very deep clauses', () => {
    const deep: ClauseItem = {
      id: 'deep',
      clause_uid: 'uid-deep',
      clause_path: 'a/b/c/d/e/f/g',
      text_content: '',
      heading_text: 'Very deep',
      numbering_label: null,
      ord: 1,
    }
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [deep], showStructure: false },
    })
    const heading = wrapper.find('[data-testid="clause-heading"]')
    expect(heading.element.tagName.toLowerCase()).toBe('h6')
  })

  it('does not render a heading element when heading_text is null', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: false },
    })
    expect(wrapper.findAll('[data-testid="clause-heading"]')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// numbering_label rendering
// ---------------------------------------------------------------------------

const LABELED_CLAUSES: ClauseItem[] = [
  {
    id: 'L1',
    clause_uid: 'uid-L1',
    clause_path: 'PART_2/11A.',
    text_content: 'Section 10(2A) of the Principal Act is amended.',
    heading_text: 'Amendment of section 10 of Principal Act',
    numbering_label: '11A.',
    ord: 1,
  },
  {
    id: 'L2',
    clause_uid: 'uid-L2',
    clause_path: 'PART_2/12./(5A)',
    text_content: 'The Minister may order amendments.',
    heading_text: null,
    numbering_label: '(5A)',
    ord: 2,
  },
  {
    id: 'L3',
    clause_uid: 'uid-L3',
    clause_path: 'PART_2/13./tail',
    text_content: 'Tail leaf with no marker.',
    heading_text: null,
    numbering_label: null,
    ord: 3,
  },
]

describe('ClauseOverlay — numbering_label', () => {
  // Regression for the "renamed clause looks identical" demo bug
  // (journal/260607-parser-heading-off-by-one.md). When two versions
  // of a clause differ only in their structural anchor (e.g. 11. →
  // 11A.), the flat-mode renderer must surface that anchor so a reader
  // can see *why* the alignment pipeline flagged the clause as MOVED.
  it('renders numbering_label as a visible prefix on the body in flat mode', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: LABELED_CLAUSES, showStructure: false },
    })
    const labels = wrapper.findAll('[data-testid="clause-numbering"]')
    // Two clauses carry a numbering_label; the third has no marker.
    expect(labels).toHaveLength(2)
    expect(labels[0]!.text()).toBe('11A.')
    expect(labels[1]!.text()).toBe('(5A)')
  })

  it('does not render numbering_label when it is null', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [LABELED_CLAUSES[2]!], showStructure: false },
    })
    expect(wrapper.findAll('[data-testid="clause-numbering"]')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// HTML body rendering & sanitization
// ---------------------------------------------------------------------------

function htmlClause(id: string, text: string): ClauseItem {
  return {
    id,
    clause_uid: `${id}-uid`,
    clause_path: id,
    text_content: text,
    heading_text: null,
    numbering_label: null,
    ord: 1,
  }
}

describe('ClauseOverlay — HTML body rendering', () => {
  it('routes plain-text bodies through the <pre> path', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: CLAUSES, showStructure: false },
    })
    // CLAUSES contain no `<` so every body renders as plain text.
    expect(wrapper.findAll('[data-testid="clause-body-text"]')).toHaveLength(CLAUSES.length)
    expect(wrapper.findAll('[data-testid="clause-body-html"]')).toHaveLength(0)
  })

  it('routes HTML-looking bodies through the sanitized v-html path', () => {
    const c = htmlClause(
      'breadcrumb',
      '<ol><li><a href="https://example.com">Home</a></li><li>Doc</li></ol>',
    )
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c], showStructure: false },
    })
    const html = wrapper.find('[data-testid="clause-body-html"]')
    expect(html.exists()).toBe(true)
    // The structural tags survived sanitization.
    expect(html.find('ol').exists()).toBe(true)
    expect(html.find('li').exists()).toBe(true)
    expect(html.find('a').exists()).toBe(true)
  })

  it('strips <script> and event-handler attributes', () => {
    const c = htmlClause(
      'evil',
      '<p>before</p><script>alert(1)</script><img src=x onerror="alert(1)"><p>after</p>',
    )
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c], showStructure: false },
    })
    const html = wrapper.find('[data-testid="clause-body-html"]')
    expect(html.html()).not.toContain('<script')
    expect(html.html()).not.toContain('onerror')
    // Benign content survives.
    expect(html.text()).toContain('before')
    expect(html.text()).toContain('after')
  })

  it('rewrites <a href> to open in a new tab with safe rel', () => {
    const c = htmlClause('link', '<a href="https://example.com">go</a>')
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c], showStructure: false },
    })
    const anchor = wrapper.find('[data-testid="clause-body-html"] a')
    expect(anchor.attributes('target')).toBe('_blank')
    expect(anchor.attributes('rel')).toBe('noopener noreferrer')
    expect(anchor.attributes('href')).toBe('https://example.com')
  })

  it('preserves table structure for fixtures that embed tables', () => {
    const c = htmlClause(
      'table',
      '<table><tr><td>cell-a</td><td>cell-b</td></tr></table>',
    )
    const wrapper = mount(ClauseOverlay, {
      props: { clauses: [c], showStructure: false },
    })
    const html = wrapper.find('[data-testid="clause-body-html"]')
    expect(html.findAll('td')).toHaveLength(2)
    expect(html.text()).toContain('cell-a')
    expect(html.text()).toContain('cell-b')
  })
})
