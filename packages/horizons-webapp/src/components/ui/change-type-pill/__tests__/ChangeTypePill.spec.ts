import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChangeTypePill from '../ChangeTypePill.vue'

describe('ChangeTypePill', () => {
  it('renders the change type label', () => {
    const wrapper = mount(ChangeTypePill, { props: { type: 'MODIFIED' } })
    expect(wrapper.text()).toBe('MODIFIED')
  })

  it('exposes the type as a data attribute for styling/testing', () => {
    const wrapper = mount(ChangeTypePill, { props: { type: 'ADDED' } })
    expect(wrapper.attributes('data-change-type')).toBe('ADDED')
  })

  it.each(['ADDED', 'REMOVED', 'MODIFIED', 'MOVED'] as const)(
    'renders %s without throwing',
    (type) => {
      const wrapper = mount(ChangeTypePill, { props: { type } })
      expect(wrapper.text()).toBe(type)
      expect(wrapper.attributes('data-change-type')).toBe(type)
    },
  )
})
