<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

interface Props {
  class?: string
}
const props = defineProps<Props>()
const classes = computed(() => cn('w-full caption-bottom text-sm', props.class))
</script>

<template>
  <div class="w-full overflow-x-auto rounded-md border border-slate-200 bg-white">
    <table :class="classes">
      <slot />
    </table>
  </div>
</template>
