// TODO(WU5.1): replace with runtime /config.json `tuningThresholds` so the
// demo can move these without a rebuild.
export const CONFIDENCE_HIGH_THRESHOLD = 0.85
export const CONFIDENCE_LOW_THRESHOLD = 0.6

export type ConfidenceTier = 'high' | 'medium' | 'low'

export function confidenceTier(value: number): ConfidenceTier {
  if (value >= CONFIDENCE_HIGH_THRESHOLD) return 'high'
  if (value >= CONFIDENCE_LOW_THRESHOLD) return 'medium'
  return 'low'
}
