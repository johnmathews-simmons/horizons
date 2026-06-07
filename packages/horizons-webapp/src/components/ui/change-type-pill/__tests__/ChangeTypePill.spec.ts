import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChangeTypePill from '../ChangeTypePill.vue'
import { CHANGE_COLORS } from '@/constants/change-colors'

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

  it.each(['ADDED', 'REMOVED', 'MODIFIED', 'MOVED'] as const)(
    'pill for %s matches CHANGE_COLORS constant',
    (type) => {
      const wrapper = mount(ChangeTypePill, { props: { type } })
      const pillClasses = CHANGE_COLORS[type].pill
      for (const cls of pillClasses.split(/\s+/)) {
        expect(wrapper.attributes('class')).toContain(cls)
      }
    },
  )
})
