<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
interface Props { class?: string }
const props = defineProps<Props>()
const classes = computed(() => cn('divide-y divide-slate-100', props.class))
</script>

<template>
  <tbody :class="classes"><slot /></tbody>
</template>
