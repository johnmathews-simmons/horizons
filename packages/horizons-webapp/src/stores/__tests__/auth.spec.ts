import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { useAuthStore } from '../auth'

const API = 'http://localhost:8000'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('login success populates the access token', async () => {
    server.use(
      http.post(`${API}/v1/auth/login`, async ({ request }) => {
        expect(request.headers.get('x-client-type')).toBe('browser')
        const body = (await request.json()) as { email: string; password: string }
        expect(body).toEqual({ email: 'alice@example.test', password: 'pw' })
        return HttpResponse.json({ access_token: 'access-1' })
      }),
    )

    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)

    await auth.login({ email: 'alice@example.test', password: 'pw' })

    expect(auth.accessToken).toBe('access-1')
    expect(auth.isAuthenticated).toBe(true)
  })

  it('logout clears the access token even when the network call fails', async () => {
    server.use(http.post(`${API}/v1/auth/logout`, () => HttpResponse.json({}, { status: 204 })))

    const auth = useAuthStore()
    auth.setAccessToken('access-2')
    expect(auth.isAuthenticated).toBe(true)

    await auth.logout()

    expect(auth.accessToken).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('refresh updates the access token from /v1/auth/refresh', async () => {
    server.use(
      http.post(`${API}/v1/auth/refresh`, () =>
        HttpResponse.json({ access_token: 'access-rotated' }),
      ),
    )

    const auth = useAuthStore()
    auth.setAccessToken('access-old')

    await auth.refresh()

    expect(auth.accessToken).toBe('access-rotated')
  })

  it('login surfaces a 401 to the caller and leaves the store unauthenticated', async () => {
    server.use(
      http.post(`${API}/v1/auth/login`, () =>
        HttpResponse.json({ detail: 'invalid credentials' }, { status: 401 }),
      ),
    )

    const auth = useAuthStore()

    await expect(auth.login({ email: 'a@b.c', password: 'wrong' })).rejects.toThrow(
      /401|invalid/i,
    )
    expect(auth.isAuthenticated).toBe(false)
  })
})
