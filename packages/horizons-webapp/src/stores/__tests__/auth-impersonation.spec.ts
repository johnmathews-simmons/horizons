/**
 * Auth-store extensions for support view:
 *
 * - kind / principal / impersonationState shape (entry + exit transitions)
 * - [[adversary class 4]] — impersonation state is never persisted to
 *   storage; a fresh store instance (~= page reload) is back to admin.
 * - [[adversary class 5]] — POST /v1/admin/impersonate failure leaves the
 *   store untouched (no impersonation state, no token swap).
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/server'
import { useAuthStore } from '../auth'
import type { MeResponse } from '@/api/me'

const API = 'http://localhost:8000'

const ADMIN_ME: MeResponse = {
  user_id: '01900000-0000-7000-8000-00000000aaaa',
  email: 'admin@example.test',
  role: 'admin',
  created_at: '2026-05-01T00:00:00Z',
  subscription: { active_pairs: [], is_admin_bypass: true },
}

const TARGET_ID = '01900000-0000-7000-8000-00000000bbbb'

function seedAdmin(): ReturnType<typeof useAuthStore> {
  const auth = useAuthStore()
  auth.setAccessToken('admin-access-token')
  auth.setPrincipal(ADMIN_ME)
  return auth
}

describe('useAuthStore — impersonation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('enterSupportView swaps to impersonation kind, captures original admin', async () => {
    server.use(
      http.post(`${API}/v1/admin/impersonate`, async ({ request }) => {
        const body = (await request.json()) as { target_user_id: string; reason: string | null }
        expect(body.target_user_id).toBe(TARGET_ID)
        return HttpResponse.json(
          {
            impersonation_token: 'imp-token-1',
            target_user_id: TARGET_ID,
            target_email: 'client@example.test',
            original_admin_id: ADMIN_ME.user_id,
            original_admin_email: ADMIN_ME.email,
            expires_in_seconds: 900,
          },
          { status: 201 },
        )
      }),
    )

    const auth = seedAdmin()
    expect(auth.kind).toBe('access')
    expect(auth.isAdmin).toBe(true)

    const response = await auth.enterSupportView(TARGET_ID, 'investigating ticket #42')

    expect(response.impersonation_token).toBe('imp-token-1')
    expect(auth.accessToken).toBe('imp-token-1')
    expect(auth.kind).toBe('impersonation')
    expect(auth.isImpersonating).toBe(true)
    expect(auth.isAdmin).toBe(false)
    expect(auth.principal?.role).toBe('client')
    expect(auth.principal?.user_id).toBe(TARGET_ID)
    expect(auth.impersonationState?.originalAccessToken).toBe('admin-access-token')
    expect(auth.impersonationState?.originalAdminEmail).toBe(ADMIN_ME.email)
    expect(auth.impersonationState?.targetEmail).toBe('client@example.test')
  })

  it('exitSupportView restores the original admin token and principal — no API call', async () => {
    // Pre-seed an impersonation state by walking through entry first.
    server.use(
      http.post(`${API}/v1/admin/impersonate`, () =>
        HttpResponse.json(
          {
            impersonation_token: 'imp-token-2',
            target_user_id: TARGET_ID,
            target_email: 'client@example.test',
            original_admin_id: ADMIN_ME.user_id,
            original_admin_email: ADMIN_ME.email,
            expires_in_seconds: 900,
          },
          { status: 201 },
        ),
      ),
    )

    const auth = seedAdmin()
    await auth.enterSupportView(TARGET_ID)

    auth.exitSupportView()

    expect(auth.accessToken).toBe('admin-access-token')
    expect(auth.kind).toBe('access')
    expect(auth.isAdmin).toBe(true)
    expect(auth.isImpersonating).toBe(false)
    expect(auth.principal).toEqual(ADMIN_ME)
    expect(auth.impersonationState).toBeNull()
  })

  it(
    '[[adversary class 4]] — a fresh store (== page reload) does NOT carry impersonation state, ' +
      'and no impersonation token is recoverable from any browser storage',
    async () => {
      // Round 1: enter support view in store A.
      server.use(
        http.post(`${API}/v1/admin/impersonate`, () =>
          HttpResponse.json(
            {
              impersonation_token: 'imp-token-3',
              target_user_id: TARGET_ID,
              target_email: 'client@example.test',
              original_admin_id: ADMIN_ME.user_id,
              original_admin_email: ADMIN_ME.email,
              expires_in_seconds: 900,
            },
            { status: 201 },
          ),
        ),
      )

      const authA = seedAdmin()
      await authA.enterSupportView(TARGET_ID)
      expect(authA.kind).toBe('impersonation')
      expect(authA.accessToken).toBe('imp-token-3')

      // Round 2: simulate a page reload by replacing the Pinia instance.
      // A genuine reload destroys the JS heap; this is the same shape.
      setActivePinia(createPinia())
      const authB = useAuthStore()

      // The new store boots empty: no token, no kind, no impersonation
      // state — exactly the cold-bootstrap entry point. The router's
      // cookie-driven refresh would then restore the *admin* bearer
      // (cookie path), never the impersonation token.
      expect(authB.accessToken).toBeNull()
      expect(authB.kind).toBe('access')
      expect(authB.isImpersonating).toBe(false)
      expect(authB.impersonationState).toBeNull()

      // Defence-in-depth: nothing impersonation-shaped is in any of the
      // browser-persistent stores. localStorage and sessionStorage are
      // the obvious leak vectors; the cookie store is not JS-readable
      // for HttpOnly cookies, so we just sweep what we *can* read.
      const lsKeys = Object.keys(localStorage)
      const ssKeys = Object.keys(sessionStorage)
      for (const key of lsKeys) {
        expect(localStorage.getItem(key) ?? '').not.toContain('imp-token-3')
      }
      for (const key of ssKeys) {
        expect(sessionStorage.getItem(key) ?? '').not.toContain('imp-token-3')
      }
    },
  )

  it(
    '[[adversary class 5]] — POST /v1/admin/impersonate failure leaves the store ' +
      'unchanged: no impersonation state, no token swap',
    async () => {
      server.use(
        http.post(`${API}/v1/admin/impersonate`, () =>
          HttpResponse.json({ detail: 'internal'  }, { status: 500 }),
        ),
      )

      const auth = seedAdmin()

      await expect(auth.enterSupportView(TARGET_ID)).rejects.toBeDefined()

      // The mint failed; the store MUST stay on the admin side. The audit
      // story (WU4.7) writes its impersonation row inside the same API
      // transaction as the token mint — no mint, no row, no banner.
      expect(auth.accessToken).toBe('admin-access-token')
      expect(auth.kind).toBe('access')
      expect(auth.isAdmin).toBe(true)
      expect(auth.isImpersonating).toBe(false)
      expect(auth.impersonationState).toBeNull()
      expect(auth.principal).toEqual(ADMIN_ME)
    },
  )

  it('enterSupportView refuses if the caller is not an admin', async () => {
    const auth = useAuthStore()
    auth.setAccessToken('client-access')
    auth.setPrincipal({
      user_id: 'client-id',
      email: 'client@example.test',
      role: 'client',
      created_at: '2026-05-01T00:00:00Z',
      subscription: { active_pairs: [], is_admin_bypass: false },
    })

    await expect(auth.enterSupportView(TARGET_ID)).rejects.toThrow(/admin/)
    expect(auth.kind).toBe('access')
    expect(auth.isImpersonating).toBe(false)
  })
})
