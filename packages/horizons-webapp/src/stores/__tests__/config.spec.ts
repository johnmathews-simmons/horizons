import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConfigStore } from '../config'
import { clearRuntimeConfig, setRuntimeConfig } from '@/config/runtime'
import type { RuntimeConfig } from '@/config/schema'

const cfg: RuntimeConfig = {
  apiBaseUrl: 'https://api.demo.test',
  tuningThresholds: { alignmentConfidence: { suppressBelow: 0.5, amberMin: 0.7, greenMin: 0.95 } },
  featureFlags: { betaAdmin: true },
}

describe('useConfigStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('exposes the runtime config through computed getters', () => {
    setRuntimeConfig(cfg)
    const store = useConfigStore()
    expect(store.apiBaseUrl).toBe(cfg.apiBaseUrl)
    expect(store.tuningThresholds.alignmentConfidence.greenMin).toBe(0.95)
    expect(store.featureFlags.betaAdmin).toBe(true)
  })

  it('reading the store before config is loaded throws the helpful error', () => {
    clearRuntimeConfig()
    const store = useConfigStore()
    expect(() => store.apiBaseUrl).toThrow(/has not been loaded/)
  })
})
