<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
interface Props { class?: string }
const props = defineProps<Props>()
const classes = computed(() => cn('px-4 py-3 text-sm text-slate-900 align-middle', props.class))
</script>

<template>
  <td :class="classes"><slot /></td>
</template>
