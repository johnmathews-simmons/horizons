import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { loginRequest, logoutRequest, refreshRequest } from '@/api/auth'
import type { LoginCredentials } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)

  const isAuthenticated = computed(() => accessToken.value !== null)

  function setAccessToken(token: string | null): void {
    accessToken.value = token
  }

  async function login(credentials: LoginCredentials): Promise<void> {
    const { access_token } = await loginRequest(credentials)
    accessToken.value = access_token
  }

  async function refresh(): Promise<void> {
    const { access_token } = await refreshRequest()
    accessToken.value = access_token
  }

  async function logout(): Promise<void> {
    try {
      await logoutRequest()
    } finally {
      accessToken.value = null
    }
  }

  function clear(): void {
    accessToken.value = null
  }

  return {
    accessToken,
    isAuthenticated,
    setAccessToken,
    login,
    refresh,
    logout,
    clear,
  }
})
