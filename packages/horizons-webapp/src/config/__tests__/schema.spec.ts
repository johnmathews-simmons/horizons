import { describe, expect, it } from 'vitest'
import { runtimeConfigSchema } from '../schema'

function validConfig(): unknown {
  return {
    apiBaseUrl: 'http://localhost:8000',
    tuningThresholds: {
      alignmentConfidence: {
        suppressBelow: 0.6,
        amberMin: 0.6,
        greenMin: 0.85,
      },
    },
    featureFlags: { highlightNew: true },
  }
}

describe('runtimeConfigSchema', () => {
  it('accepts a well-formed dev config', () => {
    const result = runtimeConfigSchema.safeParse(validConfig())
    expect(result.success).toBe(true)
  })

  it('rejects a non-URL apiBaseUrl', () => {
    const result = runtimeConfigSchema.safeParse({ ...(validConfig() as object), apiBaseUrl: 'not-a-url' })
    expect(result.success).toBe(false)
  })

  it('rejects a missing apiBaseUrl', () => {
    const cfg = validConfig() as Record<string, unknown>
    delete cfg.apiBaseUrl
    const result = runtimeConfigSchema.safeParse(cfg)
    expect(result.success).toBe(false)
  })

  it('rejects a confidence threshold > 1', () => {
    const cfg = validConfig() as { tuningThresholds: { alignmentConfidence: { greenMin: number } } }
    cfg.tuningThresholds.alignmentConfidence.greenMin = 1.5
    const result = runtimeConfigSchema.safeParse(cfg)
    expect(result.success).toBe(false)
  })

  it('rejects a confidence threshold < 0', () => {
    const cfg = validConfig() as { tuningThresholds: { alignmentConfidence: { suppressBelow: number } } }
    cfg.tuningThresholds.alignmentConfidence.suppressBelow = -0.1
    const result = runtimeConfigSchema.safeParse(cfg)
    expect(result.success).toBe(false)
  })

  it('rejects a missing tuningThresholds branch', () => {
    const cfg = validConfig() as Record<string, unknown>
    delete cfg.tuningThresholds
    const result = runtimeConfigSchema.safeParse(cfg)
    expect(result.success).toBe(false)
  })

  it('rejects a featureFlags map with non-boolean values', () => {
    const result = runtimeConfigSchema.safeParse({
      ...(validConfig() as object),
      featureFlags: { x: 'true' },
    })
    expect(result.success).toBe(false)
  })

  it('accepts an empty featureFlags map', () => {
    const result = runtimeConfigSchema.safeParse({ ...(validConfig() as object), featureFlags: {} })
    expect(result.success).toBe(true)
  })
})
