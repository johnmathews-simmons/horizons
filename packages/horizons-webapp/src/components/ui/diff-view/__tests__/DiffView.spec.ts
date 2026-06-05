import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DiffView from '../DiffView.vue'

describe('DiffView', () => {
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
