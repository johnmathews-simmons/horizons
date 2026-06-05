import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest'
import { server } from './server'
import { configureApiClient } from '@/api/client'
import { clearRuntimeConfig, setRuntimeConfig } from '@/config/runtime'
import type { RuntimeConfig } from '@/config/schema'

export const DEFAULT_TEST_CONFIG: RuntimeConfig = {
  apiBaseUrl: 'http://localhost:8000',
  tuningThresholds: {
    alignmentConfidence: {
      suppressBelow: 0.6,
      amberMin: 0.6,
      greenMin: 0.85,
    },
  },
  featureFlags: {},
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

beforeEach(() => {
  // Every test starts from the same dev-default config; bootstrap tests can
  // call clearRuntimeConfig() themselves to exercise the un-configured path.
  setRuntimeConfig(DEFAULT_TEST_CONFIG)
  configureApiClient(DEFAULT_TEST_CONFIG.apiBaseUrl)
})

afterEach(() => {
  server.resetHandlers()
  clearRuntimeConfig()
})

afterAll(() => {
  server.close()
})
