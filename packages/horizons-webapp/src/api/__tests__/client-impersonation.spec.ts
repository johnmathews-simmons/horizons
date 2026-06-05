/**
 * [[adversary class 6]] — refresh interceptor must NEVER fire for a 401
 * received while holding an impersonation bearer. This is the
 * "admin forgets they're impersonating because the cookie silently
 * re-elevated the bearer" class.
 *
 * Pins the contract from `client.ts` directly: a 401 + kind='impersonation'
 * routes through `onImpersonationExpired`, NOT `refresh`. The original
 * request is rejected (we do not retry under the elevated bearer).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { apiClient, setAuthBridge } from '../client'

const API = 'http://localhost:8000'

describe('apiClient interceptor — impersonation 401', () => {
  beforeEach(() => {
    setAuthBridge(null)
  })

  afterEach(() => {
    setAuthBridge(null)
  })

  it('does NOT call /v1/auth/refresh when kind is impersonation and a 401 fires', async () => {
    const refresh = vi.fn<() => Promise<void>>(async () => undefined)
    const onImpersonationExpired = vi.fn<() => void>()
    const onAuthFailure = vi.fn<() => void>()
    setAuthBridge({
      getAccessToken: () => 'imp-bearer',
      getKind: () => 'impersonation',
      refresh,
      onAuthFailure,
      onImpersonationExpired,
    })

    server.use(
      http.get(`${API}/v1/me`, () => HttpResponse.json({ detail: 'expired' }, { status: 401 })),
    )

    await expect(apiClient.get('/v1/me')).rejects.toMatchObject({
      response: { status: 401 },
    })

    // The defence: refresh() is NEVER called under an impersonation bearer.
    // The cookie belongs to the original admin; calling /v1/auth/refresh
    // would silently re-elevate the bearer to admin context while the SPA
    // still rendered support view.
    expect(refresh).not.toHaveBeenCalled()
    expect(onImpersonationExpired).toHaveBeenCalledOnce()
    expect(onAuthFailure).not.toHaveBeenCalled()
  })

  it('still calls refresh when kind is access on 401 (regression net)', async () => {
    let tokenAfterRefresh = false
    const refresh = vi.fn<() => Promise<void>>(async () => {
      tokenAfterRefresh = true
    })
    const onImpersonationExpired = vi.fn<() => void>()
    setAuthBridge({
      getAccessToken: () => (tokenAfterRefresh ? 'rotated' : 'stale'),
      getKind: () => 'access',
      refresh,
      onAuthFailure: vi.fn<() => void>(),
      onImpersonationExpired,
    })

    server.use(
      http.get(`${API}/v1/me`, ({ request }) => {
        if (request.headers.get('authorization') === 'Bearer rotated') {
          return HttpResponse.json({ ok: true })
        }
        return HttpResponse.json({ detail: 'expired' }, { status: 401 })
      }),
    )

    const response = await apiClient.get('/v1/me')

    expect(response.status).toBe(200)
    expect(refresh).toHaveBeenCalledOnce()
    expect(onImpersonationExpired).not.toHaveBeenCalled()
  })
})
