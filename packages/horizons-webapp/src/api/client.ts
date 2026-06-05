import axios, { type AxiosError, type AxiosResponse } from 'axios'

// Module augmentation: add transport-only flags read by the interceptor
// below. `<D = any>` mirrors axios's own generic signature so the merge takes;
// the no-explicit-any guard would otherwise force `unknown` and prevent the
// merge.
/* eslint-disable @typescript-eslint/no-explicit-any */
declare module 'axios' {
  export interface AxiosRequestConfig<D = any> {
    _retried?: boolean
    _skipAuthRefresh?: boolean
  }
  export interface InternalAxiosRequestConfig<D = any> {
    _retried?: boolean
    _skipAuthRefresh?: boolean
  }
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export type BearerKind = 'access' | 'impersonation'

export interface AuthBridge {
  getAccessToken: () => string | null
  /**
   * Returns the kind of bearer currently held. The refresh interceptor
   * uses this to decide whether a 401 is recoverable via /v1/auth/refresh
   * (`access`) or must instead drop the impersonation token and bounce
   * the admin out of support view (`impersonation`). See [[adversary
   * class 6]] in the WU5.4 journal — without this gate the cookie-based
   * refresh would silently re-elevate the admin to admin context while
   * the SPA's UI still believes it is in support view.
   */
  getKind: () => BearerKind
  refresh: () => Promise<void>
  onAuthFailure: () => void
  /**
   * Called when a 401 fires while holding an impersonation bearer.
   * Bootstrap wires this to: clear impersonation state, navigate to
   * /admin/clients, surface a "Support view expired" toast. The original
   * request is then rejected (the impersonation TTL is the bound — we
   * do NOT retry).
   */
  onImpersonationExpired: () => void
}

let bridge: AuthBridge | null = null

export function setAuthBridge(next: AuthBridge | null): void {
  bridge = next
}

export const apiClient = axios.create({
  withCredentials: true,
  headers: { Accept: 'application/json' },
})

// Set the API base URL from runtime config (loaded by bootstrap()) before any
// HTTP call. Without this every request fails against the page origin, which
// is the intentional fail-loud signal — never silently default.
export function configureApiClient(baseUrl: string): void {
  apiClient.defaults.baseURL = baseUrl
}

apiClient.interceptors.request.use((config) => {
  // _skipAuthRefresh marks the three auth endpoints (login / refresh /
  // logout). For those, the refresh cookie is the authoritative source;
  // an Authorization access token would win precedence on the API
  // (deps/refresh.py _extract_refresh_token) and fail the kind check.
  const token = bridge?.getAccessToken() ?? null
  if (token && !config._skipAuthRefresh) {
    config.headers.set('Authorization', `Bearer ${token}`)
  } else {
    config.headers.delete('Authorization')
  }
  return config
})

let inFlightRefresh: Promise<void> | null = null

function singleFlightRefresh(): Promise<void> {
  if (!bridge) {
    return Promise.reject(new Error('auth bridge not registered'))
  }
  if (inFlightRefresh === null) {
    inFlightRefresh = bridge.refresh().finally(() => {
      inFlightRefresh = null
    })
  }
  return inFlightRefresh
}

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const config = error.config
    if (!config || error.response?.status !== 401 || config._skipAuthRefresh || config._retried) {
      return Promise.reject(error)
    }
    // [[adversary class 6]] — an impersonation bearer's 401 must NEVER
    // trigger /v1/auth/refresh. The refresh cookie belongs to the
    // original admin; calling refresh would silently re-elevate the
    // bearer to admin context while the UI still renders the support
    // view. Instead: hand off to the impersonation-expired handler
    // (which exits support view + toasts + navigates) and reject.
    if (bridge?.getKind() === 'impersonation') {
      bridge.onImpersonationExpired()
      return Promise.reject(error)
    }
    config._retried = true
    try {
      await singleFlightRefresh()
    } catch (refreshError) {
      bridge?.onAuthFailure()
      return Promise.reject(refreshError)
    }
    return apiClient.request(config)
  },
)
