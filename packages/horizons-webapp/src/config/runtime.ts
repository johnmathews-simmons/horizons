import { runtimeConfigSchema, type RuntimeConfig } from './schema'

let current: RuntimeConfig | null = null

export function setRuntimeConfig(config: RuntimeConfig): void {
  current = config
}

export function clearRuntimeConfig(): void {
  current = null
}

export function hasRuntimeConfig(): boolean {
  return current !== null
}

export function getRuntimeConfig(): RuntimeConfig {
  if (current === null) {
    throw new Error(
      'runtime config has not been loaded; bootstrap() must run before any code that reads config',
    )
  }
  return current
}

export const CONFIG_URL = '/config.json'

export async function fetchAndValidateConfig(url: string = CONFIG_URL): Promise<RuntimeConfig> {
  let response: Response
  try {
    response = await fetch(url, { cache: 'no-store' })
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err)
    throw new Error(`network error fetching ${url}: ${reason}`)
  }
  if (!response.ok) {
    throw new Error(`fetch ${url} returned HTTP ${response.status}`)
  }
  let parsed: unknown
  try {
    parsed = await response.json()
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err)
    throw new Error(`could not parse ${url} as JSON: ${reason}`)
  }
  const result = runtimeConfigSchema.safeParse(parsed)
  if (!result.success) {
    const issues = result.error.issues
      .map((issue) => `${issue.path.join('.') || '<root>'}: ${issue.message}`)
      .join('; ')
    throw new Error(`${url} failed schema validation: ${issues}`)
  }
  return result.data
}
