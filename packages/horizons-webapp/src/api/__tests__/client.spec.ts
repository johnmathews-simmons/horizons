import { describe, expect, it } from 'vitest'
import { ApiClient, ApiError } from '../client'

describe('ApiClient', () => {
  it('throws if VITE_API_BASE_URL is missing', () => {
    expect(() => new ApiClient({ baseUrl: '' })).toThrowError(/VITE_API_BASE_URL/)
  })

  it('builds a GET request against the configured base URL', async () => {
    let capturedUrl: string | undefined
    let capturedMethod: string | undefined
    const fetchImpl = (async (input: URL | RequestInfo, init?: RequestInit) => {
      capturedUrl = input instanceof URL ? input.toString() : String(input)
      capturedMethod = init?.method
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    const client = new ApiClient({ baseUrl: 'https://api.example/', fetchImpl })
    const result = await client.get<{ ok: boolean }>('/health', { ping: 1 })

    expect(capturedUrl).toBe('https://api.example/health?ping=1')
    expect(capturedMethod).toBe('GET')
    expect(result).toEqual({ ok: true })
  })

  it('throws ApiError on non-2xx responses', async () => {
    const fetchImpl = (async () =>
      new Response('boom', { status: 500, statusText: 'Server Error' })) as typeof fetch
    const client = new ApiClient({ baseUrl: 'https://api.example', fetchImpl })
    await expect(client.get('/x')).rejects.toBeInstanceOf(ApiError)
  })
})
