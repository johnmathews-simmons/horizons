import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { loginRequest, logoutRequest, refreshRequest } from '@/api/auth'
import type { LoginCredentials } from '@/api/auth'
import { fetchMe, type MeResponse } from '@/api/me'
import { postImpersonate, type ImpersonateResponse } from '@/api/admin'

export type TokenKind = 'access' | 'impersonation'

/**
 * The in-memory snapshot the SPA needs to render the support-view banner
 * (target email, original admin email) AND to restore admin context on
 * exit / expiry without re-authenticating. Held in memory only — never
 * persisted to storage. A page reload destroys this and the cookie-based
 * cold bootstrap re-enters the SPA as the original admin, never as the
 * impersonated client. See [[adversary class 4]] in the WU5.4 journal.
 */
export interface ImpersonationState {
  targetUserId: string
  targetEmail: string
  originalAdminId: string
  originalAdminEmail: string
  /** The admin's access token captured at entry; restored verbatim on exit. */
  originalAccessToken: string
  /** The admin's principal captured at entry; restored on exit. */
  originalPrincipal: MeResponse
  enteredAt: number
  expiresAt: number
}

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const kind = ref<TokenKind>('access')
  const principal = ref<MeResponse | null>(null)
  const impersonationState = ref<ImpersonationState | null>(null)

  const isAuthenticated = computed(() => accessToken.value !== null)
  const isAdmin = computed(() => principal.value?.role === 'admin' && kind.value === 'access')
  const isImpersonating = computed(() => impersonationState.value !== null)

  function setAccessToken(token: string | null): void {
    accessToken.value = token
    if (token === null) {
      kind.value = 'access'
      principal.value = null
      impersonationState.value = null
    }
  }

  function setPrincipal(next: MeResponse | null): void {
    principal.value = next
  }

  async function refreshPrincipal(): Promise<MeResponse | null> {
    if (accessToken.value === null) return null
    try {
      const me = await fetchMe()
      principal.value = me
      return me
    } catch {
      // /v1/me failure does not invalidate the auth state — the bearer
      // may still be valid; caller decides what to render.
      return null
    }
  }

  async function login(credentials: LoginCredentials): Promise<void> {
    const { access_token } = await loginRequest(credentials)
    accessToken.value = access_token
    kind.value = 'access'
    impersonationState.value = null
    await refreshPrincipal()
  }

  async function refresh(): Promise<void> {
    const { access_token } = await refreshRequest()
    accessToken.value = access_token
    kind.value = 'access'
    impersonationState.value = null
    await refreshPrincipal()
  }

  async function logout(): Promise<void> {
    try {
      await logoutRequest()
    } finally {
      accessToken.value = null
      kind.value = 'access'
      principal.value = null
      impersonationState.value = null
    }
  }

  function clear(): void {
    accessToken.value = null
    kind.value = 'access'
    principal.value = null
    impersonationState.value = null
  }

  /**
   * Mint an impersonation token and swap the bearer in-memory.
   *
   * Preconditions: caller is an authenticated admin (`isAdmin === true`).
   * The route guard enforces this for /admin/* surfaces; the store
   * additionally refuses if the principal isn't an admin so a stray
   * call site can't smuggle an impersonation request from a client
   * session.
   *
   * On a non-201 response, throws and leaves the store unchanged —
   * see [[adversary class 5]]: a network blip mid-mint must not leave
   * the SPA in support view without a paired audit row.
   */
  async function enterSupportView(
    targetUserId: string,
    reason?: string,
  ): Promise<ImpersonateResponse> {
    if (kind.value !== 'access' || principal.value?.role !== 'admin') {
      throw new Error('enterSupportView requires an active admin session')
    }
    const originalAccessToken = accessToken.value
    const originalPrincipal = principal.value
    if (originalAccessToken === null) {
      throw new Error('enterSupportView requires a non-null bearer')
    }
    const response = await postImpersonate({
      target_user_id: targetUserId,
      reason: reason ?? null,
    })
    const enteredAt = Date.now()
    impersonationState.value = {
      targetUserId: response.target_user_id,
      targetEmail: response.target_email,
      originalAdminId: response.original_admin_id,
      originalAdminEmail: response.original_admin_email,
      originalAccessToken,
      originalPrincipal,
      enteredAt,
      expiresAt: enteredAt + response.expires_in_seconds * 1000,
    }
    accessToken.value = response.impersonation_token
    kind.value = 'impersonation'
    // Synthesize a client-shaped principal for the support-view UI so
    // /me-driven views stop showing admin context. The real /v1/me call
    // under the impersonation bearer would return the same shape (the
    // API resolves the principal from the bearer's user_id claim).
    principal.value = {
      user_id: response.target_user_id,
      email: response.target_email,
      role: 'client',
      created_at: originalPrincipal.created_at,
      subscription: { active_pairs: [], is_admin_bypass: false },
    }
    return response
  }

  /**
   * Drop the impersonation bearer and restore the original admin context
   * from the captured in-memory snapshot. No server call: the API does
   * not expose a /v1/admin/impersonate/exit endpoint (the entry audit
   * row + the 15-minute TTL are the durable record — see WU4.7 journal).
   */
  function exitSupportView(): void {
    const snapshot = impersonationState.value
    if (snapshot === null) return
    accessToken.value = snapshot.originalAccessToken
    kind.value = 'access'
    principal.value = snapshot.originalPrincipal
    impersonationState.value = null
  }

  return {
    accessToken,
    kind,
    principal,
    impersonationState,
    isAuthenticated,
    isAdmin,
    isImpersonating,
    setAccessToken,
    setPrincipal,
    refreshPrincipal,
    login,
    refresh,
    logout,
    clear,
    enterSupportView,
    exitSupportView,
  }
})
