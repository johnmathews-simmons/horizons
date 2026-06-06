import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SectorCard from '../SectorCard.vue'

describe('SectorCard', () => {
  it('renders the code and document count', () => {
    const wrapper = mount(SectorCard, {
      props: { code: 'BANKING', documentCount: 3, subscribed: true },
    })
    expect(wrapper.text()).toContain('BANKING')
    expect(wrapper.text()).toContain('3')
  })

  it('shows the Not subscribed badge when not subscribed', () => {
    const wrapper = mount(SectorCard, {
      props: { code: 'employment', documentCount: 1, subscribed: false },
    })
    expect(wrapper.text()).toContain('Not subscribed')
  })

  it('emits select on click when subscribed', async () => {
    const wrapper = mount(SectorCard, {
      props: { code: 'BANKING', documentCount: 3, subscribed: true },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['BANKING']])
  })

  it('does not emit select on click when not subscribed', async () => {
    const wrapper = mount(SectorCard, {
      props: { code: 'employment', documentCount: 1, subscribed: false },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('sets a tooltip on the not-subscribed state', () => {
    const wrapper = mount(SectorCard, {
      props: { code: 'employment', documentCount: 1, subscribed: false },
    })
    expect(wrapper.attributes('title')).toBe('Subscribe to view')
  })
})
