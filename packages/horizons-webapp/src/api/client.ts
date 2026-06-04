/**
 * Typed fetch wrapper for the Horizons public REST API.
 *
 * The webapp is a customer of the same API every external integrator uses
 * (see docs/4. services.md). All HTTP calls go through this client so auth,
 * base-URL, and error handling have a single home.
 *
 * No endpoint calls live here yet — the API surface lands in later work
 * units. This is the seam they will hang off.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly statusText: string,
    readonly body: unknown,
  ) {
    super(`Horizons API ${status} ${statusText}`)
    this.name = 'ApiError'
  }
}

export interface ApiClientOptions {
  /** Base URL, no trailing slash. Defaults to `import.meta.env.VITE_API_BASE_URL`. */
  baseUrl?: string
  /** Bearer token, if the caller is authenticated. */
  token?: string | null
  /** Override `fetch` for testing. Defaults to the global. */
  fetchImpl?: typeof fetch
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly token: string | null
  private readonly fetchImpl: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    const baseUrl = options.baseUrl ?? import.meta.env.VITE_API_BASE_URL
    if (!baseUrl) {
      throw new Error(
        'ApiClient: VITE_API_BASE_URL is not set. Add it to .env (see .env.example).',
      )
    }
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.token = options.token ?? null
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
  }

  async request<T>(
    method: string,
    path: string,
    init: { body?: unknown; query?: Record<string, string | number | boolean> } = {},
  ): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`)
    if (init.query) {
      for (const [k, v] of Object.entries(init.query)) {
        url.searchParams.set(k, String(v))
      }
    }
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    if (init.body !== undefined) headers['Content-Type'] = 'application/json'

    const response = await this.fetchImpl(url, {
      method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    })

    if (!response.ok) {
      const body = await response.text().then((t) => {
        try {
          return JSON.parse(t)
        } catch {
          return t
        }
      })
      throw new ApiError(response.status, response.statusText, body)
    }

    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  get<T>(path: string, query?: Record<string, string | number | boolean>): Promise<T> {
    return this.request<T>('GET', path, { query })
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('POST', path, { body })
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PUT', path, { body })
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>('DELETE', path)
  }
}

let _default: ApiClient | null = null

/**
 * Lazy singleton — constructed on first use so tests can import the module
 * without `VITE_API_BASE_URL` set. Pass `options` on the first call to
 * override defaults; subsequent calls ignore arguments.
 */
export function getApiClient(options?: ApiClientOptions): ApiClient {
  if (_default === null) _default = new ApiClient(options)
  return _default
}
