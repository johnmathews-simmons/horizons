<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

interface Props { class?: string }
const props = defineProps<Props>()
const classes = computed(() => cn('border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-500', props.class))
</script>

<template>
  <thead :class="classes"><slot /></thead>
</template>
