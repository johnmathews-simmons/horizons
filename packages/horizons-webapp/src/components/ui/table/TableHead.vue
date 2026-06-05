<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
interface Props { class?: string }
const props = defineProps<Props>()
const classes = computed(() => cn('px-4 py-2 text-left font-medium tracking-wide', props.class))
</script>

<template>
  <th scope="col" :class="classes"><slot /></th>
</template>
