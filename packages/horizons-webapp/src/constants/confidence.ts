import { getRuntimeConfig } from '@/config/runtime'

export type ConfidenceTier = 'high' | 'medium' | 'low'

export function confidenceTier(value: number): ConfidenceTier {
  const { amberMin, greenMin } = getRuntimeConfig().tuningThresholds.alignmentConfidence
  if (value >= greenMin) return 'high'
  if (value >= amberMin) return 'medium'
  return 'low'
}

export function suppressBelowThreshold(): number {
  return getRuntimeConfig().tuningThresholds.alignmentConfidence.suppressBelow
}
