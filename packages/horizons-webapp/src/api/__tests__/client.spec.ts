import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { apiClient, setAuthBridge } from '../client'

const API = 'http://localhost:8000'

describe('apiClient refresh interceptor', () => {
  beforeEach(() => {
    setAuthBridge(null)
  })

  afterEach(() => {
    setAuthBridge(null)
  })

  it('injects the bearer token from the auth bridge', async () => {
    let observed: string | null = null
    setAuthBridge({
      getAccessToken: () => 'access-1',
      refresh: vi.fn<() => Promise<void>>(async () => undefined),
      onAuthFailure: vi.fn<() => void>(),
    })

    server.use(
      http.get(`${API}/v1/me`, ({ request }) => {
        observed = request.headers.get('authorization')
        return HttpResponse.json({ ok: true })
      }),
    )

    const response = await apiClient.get('/v1/me')

    expect(response.status).toBe(200)
    expect(observed).toBe('Bearer access-1')
  })

  it('on 401, refreshes once via the bridge and retries the original request', async () => {
    let tokenAfterRefresh = false
    const refresh = vi.fn<() => Promise<void>>(async () => {
      tokenAfterRefresh = true
    })
    setAuthBridge({
      getAccessToken: () => (tokenAfterRefresh ? 'access-rotated' : 'access-stale'),
      refresh,
      onAuthFailure: vi.fn<() => void>(),
    })

    let attempts = 0
    server.use(
      http.get(`${API}/v1/me`, ({ request }) => {
        attempts += 1
        const auth = request.headers.get('authorization')
        if (auth === 'Bearer access-rotated') {
          return HttpResponse.json({ ok: true })
        }
        return HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })
      }),
    )

    const response = await apiClient.get('/v1/me')

    expect(attempts).toBe(2)
    expect(refresh).toHaveBeenCalledOnce()
    expect(response.status).toBe(200)
  })

  it('on a second 401 after refresh, rejects without re-entering refresh', async () => {
    const refresh = vi.fn<() => Promise<void>>(async () => undefined)
    const onAuthFailure = vi.fn<() => void>()
    setAuthBridge({
      getAccessToken: () => 'access-stale',
      refresh,
      onAuthFailure,
    })

    server.use(
      http.get(`${API}/v1/me`, () =>
        HttpResponse.json({ detail: 'unauthorized' }, { status: 401 }),
      ),
    )

    await expect(apiClient.get('/v1/me')).rejects.toMatchObject({
      response: { status: 401 },
    })

    expect(refresh).toHaveBeenCalledOnce()
    expect(onAuthFailure).not.toHaveBeenCalled()
  })

  it('when refresh itself rejects, calls onAuthFailure and rejects', async () => {
    const refresh = vi.fn<() => Promise<void>>(async () => {
      throw new Error('refresh failed')
    })
    const onAuthFailure = vi.fn<() => void>()
    setAuthBridge({
      getAccessToken: () => 'access-stale',
      refresh,
      onAuthFailure,
    })

    server.use(
      http.get(`${API}/v1/me`, () =>
        HttpResponse.json({ detail: 'unauthorized' }, { status: 401 }),
      ),
    )

    await expect(apiClient.get('/v1/me')).rejects.toThrow('refresh failed')
    expect(onAuthFailure).toHaveBeenCalledOnce()
  })

  it('coalesces concurrent 401s into a single refresh (single-flight)', async () => {
    let tokenAfterRefresh = false
    const refresh = vi.fn<() => Promise<void>>(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
      tokenAfterRefresh = true
    })
    setAuthBridge({
      getAccessToken: () => (tokenAfterRefresh ? 'access-rotated' : 'access-stale'),
      refresh,
      onAuthFailure: vi.fn<() => void>(),
    })

    server.use(
      http.get(`${API}/v1/me`, ({ request }) => {
        const auth = request.headers.get('authorization')
        if (auth === 'Bearer access-rotated') {
          return HttpResponse.json({ ok: true })
        }
        return HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })
      }),
    )

    const [a, b, c] = await Promise.all([
      apiClient.get('/v1/me'),
      apiClient.get('/v1/me'),
      apiClient.get('/v1/me'),
    ])

    expect(refresh).toHaveBeenCalledOnce()
    expect(a.status).toBe(200)
    expect(b.status).toBe(200)
    expect(c.status).toBe(200)
  })
})
