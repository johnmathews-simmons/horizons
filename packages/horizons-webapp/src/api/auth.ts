import { apiClient } from './client'

export interface LoginCredentials {
  email: string
  password: string
}

export interface AuthTokenResponse {
  access_token: string
}

const BROWSER_CLIENT_HEADERS = { 'X-Client-Type': 'browser' } as const

export async function loginRequest(credentials: LoginCredentials): Promise<AuthTokenResponse> {
  const response = await apiClient.post<AuthTokenResponse>('/v1/auth/login', credentials, {
    headers: BROWSER_CLIENT_HEADERS,
    _skipAuthRefresh: true,
  })
  return response.data
}

export async function refreshRequest(): Promise<AuthTokenResponse> {
  const response = await apiClient.post<AuthTokenResponse>('/v1/auth/refresh', undefined, {
    _skipAuthRefresh: true,
  })
  return response.data
}

export async function logoutRequest(): Promise<void> {
  await apiClient.post('/v1/auth/logout', undefined, { _skipAuthRefresh: true })
}
