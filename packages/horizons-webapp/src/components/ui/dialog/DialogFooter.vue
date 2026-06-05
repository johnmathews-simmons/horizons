<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

interface Props {
  class?: string
}
const props = defineProps<Props>()
const classes = computed(() => cn('flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', props.class))
</script>

<template>
  <div :class="classes">
    <slot />
  </div>
</template>
