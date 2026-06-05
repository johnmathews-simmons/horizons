import { z } from 'zod'

const probability = z.number().min(0).max(1)

export const runtimeConfigSchema = z.object({
  apiBaseUrl: z.url(),
  tuningThresholds: z.object({
    alignmentConfidence: z.object({
      suppressBelow: probability,
      amberMin: probability,
      greenMin: probability,
    }),
  }),
  featureFlags: z.record(z.string(), z.boolean()),
})

export type RuntimeConfig = z.infer<typeof runtimeConfigSchema>
