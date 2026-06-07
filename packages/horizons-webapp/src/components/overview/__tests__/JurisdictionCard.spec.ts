import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import JurisdictionCard from '../JurisdictionCard.vue'

describe('JurisdictionCard', () => {
  it('renders the code and document count', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, changeCount: 2, subscribed: true },
    })
    expect(wrapper.text()).toContain('UK')
    expect(wrapper.text()).toContain('3')
  })

  it('renders the recent change count', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, changeCount: 2, subscribed: true },
    })
    expect(wrapper.text()).toMatch(/2\s+recent\s+changes/)
  })

  it('uses singular "change" when changeCount is 1', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, changeCount: 1, subscribed: true },
    })
    expect(wrapper.text()).toMatch(/1\s+recent\s+change(?!s)/)
  })

  it('shows the Not subscribed badge when not subscribed', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, changeCount: 0, subscribed: false },
    })
    expect(wrapper.text()).toContain('Not subscribed')
  })

  it('emits select with code and changeCount on click when subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, changeCount: 4, subscribed: true },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['UK', 4]])
  })

  it('does not emit select on click when not subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, changeCount: 0, subscribed: false },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('sets a tooltip on the not-subscribed state', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, changeCount: 0, subscribed: false },
    })
    expect(wrapper.attributes('title')).toBe('Subscribe to view')
  })
})
