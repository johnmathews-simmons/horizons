<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { confidenceTier, type ConfidenceTier } from '@/constants/confidence'

interface Props {
  value: number
  class?: string
}

const props = defineProps<Props>()

const tier = computed<ConfidenceTier>(() => confidenceTier(props.value))
const display = computed(() => props.value.toFixed(2))

const tierClasses: Record<ConfidenceTier, string> = {
  high: 'bg-green-100 text-green-900 ring-green-300',
  medium: 'bg-amber-100 text-amber-900 ring-amber-300',
  low: 'bg-red-100 text-red-900 ring-red-300',
}

const classes = computed(() =>
  cn(
    'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium tabular-nums ring-1 ring-inset',
    tierClasses[tier.value],
    props.class,
  ),
)
</script>

<template>
  <span :class="classes" :data-confidence="tier">{{ display }}</span>
</template>
