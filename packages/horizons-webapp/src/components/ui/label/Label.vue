<script setup lang="ts">
import { computed } from 'vue'
import { Label as RekaLabel } from 'reka-ui'
import { cn } from '@/lib/utils'

interface Props {
  for?: string
  class?: string
}

const props = defineProps<Props>()

const classes = computed(() =>
  cn(
    'text-sm font-medium leading-none text-slate-900 peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
    props.class,
  ),
)
</script>

<template>
  <RekaLabel :for="props.for" :class="classes">
    <slot />
  </RekaLabel>
</template>
