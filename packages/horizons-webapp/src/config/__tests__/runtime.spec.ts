import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import {
  CONFIG_URL,
  clearRuntimeConfig,
  fetchAndValidateConfig,
  getRuntimeConfig,
  hasRuntimeConfig,
  setRuntimeConfig,
} from '../runtime'
import type { RuntimeConfig } from '../schema'

const validBody: RuntimeConfig = {
  apiBaseUrl: 'https://api.example.com',
  tuningThresholds: {
    alignmentConfidence: { suppressBelow: 0.55, amberMin: 0.65, greenMin: 0.9 },
  },
  featureFlags: { adminView: false },
}

describe('fetchAndValidateConfig', () => {
  beforeEach(() => {
    clearRuntimeConfig()
  })

  afterEach(() => {
    clearRuntimeConfig()
  })

  it('returns the parsed config on a 200 with a valid body', async () => {
    server.use(http.get(CONFIG_URL, () => HttpResponse.json(validBody)))
    const config = await fetchAndValidateConfig()
    expect(config).toStrictEqual(validBody)
  })

  it('throws on HTTP 500', async () => {
    server.use(http.get(CONFIG_URL, () => HttpResponse.text('server error', { status: 500 })))
    await expect(fetchAndValidateConfig()).rejects.toThrow(/HTTP 500/)
  })

  it('throws on schema mismatch', async () => {
    server.use(http.get(CONFIG_URL, () => HttpResponse.json({ apiBaseUrl: 'http://localhost' })))
    await expect(fetchAndValidateConfig()).rejects.toThrow(/schema validation/)
  })

  it('throws on non-JSON body', async () => {
    server.use(
      http.get(CONFIG_URL, () =>
        HttpResponse.text('<!doctype html><body>oops', {
          status: 200,
          headers: { 'content-type': 'text/html' },
        }),
      ),
    )
    await expect(fetchAndValidateConfig()).rejects.toThrow(/JSON/)
  })

  it('throws on network failure', async () => {
    server.use(http.get(CONFIG_URL, () => HttpResponse.error()))
    await expect(fetchAndValidateConfig()).rejects.toThrow(/network error/)
  })

  it('error message names the URL so deploy-mismatch surfaces in the error screen', async () => {
    server.use(http.get(CONFIG_URL, () => HttpResponse.json({ apiBaseUrl: 'nope' })))
    await expect(fetchAndValidateConfig()).rejects.toThrow(/\/config\.json/)
  })
})

describe('runtime config singleton', () => {
  beforeEach(() => {
    clearRuntimeConfig()
  })

  it('getRuntimeConfig throws before set', () => {
    expect(hasRuntimeConfig()).toBe(false)
    expect(() => getRuntimeConfig()).toThrow(/has not been loaded/)
  })

  it('round-trips a config through set/get', () => {
    setRuntimeConfig(validBody)
    expect(hasRuntimeConfig()).toBe(true)
    expect(getRuntimeConfig()).toStrictEqual(validBody)
  })
})
