import { computed } from 'vue'
import { defineStore } from 'pinia'
import { getRuntimeConfig } from '@/config/runtime'

export const useConfigStore = defineStore('config', () => {
  const config = computed(() => getRuntimeConfig())
  const apiBaseUrl = computed(() => config.value.apiBaseUrl)
  const tuningThresholds = computed(() => config.value.tuningThresholds)
  const featureFlags = computed(() => config.value.featureFlags)
  return { config, apiBaseUrl, tuningThresholds, featureFlags }
})
