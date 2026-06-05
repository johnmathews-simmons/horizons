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

export interface AuthBridge {
  getAccessToken: () => string | null
  refresh: () => Promise<void>
  onAuthFailure: () => void
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
