import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfidenceBadge from '../ConfidenceBadge.vue'

describe('ConfidenceBadge', () => {
  it('renders the value as a two-decimal float', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.923 } })
    expect(wrapper.text()).toBe('0.92')
  })

  it('green for high confidence (>= 0.85)', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.91 } })
    expect(wrapper.attributes('data-confidence')).toBe('high')
    expect(wrapper.classes().some((c) => c.includes('green'))).toBe(true)
  })

  it('amber for medium confidence (>= 0.6 and < 0.85)', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.7 } })
    expect(wrapper.attributes('data-confidence')).toBe('medium')
    expect(wrapper.classes().some((c) => c.includes('amber'))).toBe(true)
  })

  it('red for low confidence (< 0.6)', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.4 } })
    expect(wrapper.attributes('data-confidence')).toBe('low')
    expect(wrapper.classes().some((c) => c.includes('red'))).toBe(true)
  })

  it('boundary value 0.85 counts as high', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.85 } })
    expect(wrapper.attributes('data-confidence')).toBe('high')
  })

  it('boundary value 0.6 counts as medium', () => {
    const wrapper = mount(ConfidenceBadge, { props: { value: 0.6 } })
    expect(wrapper.attributes('data-confidence')).toBe('medium')
  })
})
