<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
interface Props { class?: string }
const props = defineProps<Props>()
const classes = computed(() => cn('transition-colors hover:bg-slate-50', props.class))
</script>

<template>
  <tr :class="classes"><slot /></tr>
</template>
