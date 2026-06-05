import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse, delay } from 'msw'
import { server } from '@/test/server'
import { bootstrap } from '@/bootstrap'
import { apiClient } from '@/api/client'
import { CONFIG_URL, clearRuntimeConfig, hasRuntimeConfig } from '@/config/runtime'

function setupRoot(): HTMLElement {
  document.body.innerHTML = '<div id="app"></div>'
  const root = document.getElementById('app')
  if (!root) throw new Error('test setup failed: #app missing')
  return root
}

const validConfig = {
  apiBaseUrl: 'https://api.example.test',
  tuningThresholds: {
    alignmentConfidence: { suppressBelow: 0.5, amberMin: 0.6, greenMin: 0.9 },
  },
  featureFlags: {},
}

describe('bootstrap()', () => {
  beforeEach(() => {
    clearRuntimeConfig()
    apiClient.defaults.baseURL = undefined
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('mounts the app and configures the api client when config loads cleanly', async () => {
    const root = setupRoot()
    server.use(http.get(CONFIG_URL, () => HttpResponse.json(validConfig)))

    const result = await bootstrap()

    expect(result.status).toBe('mounted')
    expect(hasRuntimeConfig()).toBe(true)
    expect(apiClient.defaults.baseURL).toBe(validConfig.apiBaseUrl)
    // Vue mounted: the LoginView renders the 'Sign in' heading on the
    // unauthenticated `/login` route the guard redirects to.
    expect(root.querySelector('[data-testid="config-error"]')).toBeNull()
    expect(root.innerHTML).not.toBe('')
  })

  it('renders a fail-loud error screen and does not mount on fetch failure', async () => {
    const root = setupRoot()
    server.use(http.get(CONFIG_URL, () => HttpResponse.text('boom', { status: 503 })))

    const result = await bootstrap()

    expect(result.status).toBe('failed')
    expect(hasRuntimeConfig()).toBe(false)
    expect(apiClient.defaults.baseURL).toBeUndefined()
    expect(root.querySelector('[data-testid="config-error"]')).not.toBeNull()
    expect(root.querySelector('[data-testid="config-error-reason"]')?.textContent).toMatch(/HTTP 503/)
  })

  it('renders a fail-loud error screen on schema validation failure', async () => {
    const root = setupRoot()
    server.use(http.get(CONFIG_URL, () => HttpResponse.json({ apiBaseUrl: 'not-a-url' })))

    const result = await bootstrap()

    expect(result.status).toBe('failed')
    expect(root.querySelector('[data-testid="config-error-reason"]')?.textContent).toMatch(
      /schema validation/,
    )
  })

  it('nothing is rendered until the config fetch resolves', async () => {
    const root = setupRoot()
    server.use(
      http.get(CONFIG_URL, async () => {
        await delay(40)
        return HttpResponse.json(validConfig)
      }),
    )

    const pending = bootstrap()
    expect(root.innerHTML).toBe('')
    expect(hasRuntimeConfig()).toBe(false)

    const result = await pending

    expect(result.status).toBe('mounted')
    expect(hasRuntimeConfig()).toBe(true)
    expect(root.innerHTML).not.toBe('')
  })

  it('returns failed and does not mount when the root element is missing', async () => {
    document.body.innerHTML = ''
    const result = await bootstrap('#app')
    expect(result.status).toBe('failed')
    expect(result.reason).toMatch(/not found/)
  })
})
