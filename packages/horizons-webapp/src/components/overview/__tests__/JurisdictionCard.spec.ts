import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import JurisdictionCard from '../JurisdictionCard.vue'

describe('JurisdictionCard', () => {
  it('renders the code and document count', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, subscribed: true },
    })
    expect(wrapper.text()).toContain('UK')
    expect(wrapper.text()).toContain('3')
  })

  it('shows the Not subscribed badge when not subscribed', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    expect(wrapper.text()).toContain('Not subscribed')
  })

  it('emits select on click when subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, subscribed: true },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['UK']])
  })

  it('does not emit select on click when not subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('sets a tooltip on the not-subscribed state', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    expect(wrapper.attributes('title')).toBe('Subscribe to view')
  })
})
