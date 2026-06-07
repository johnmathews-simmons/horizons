<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

export type { ChangeType }

interface Props {
  type: ChangeType
  class?: string
}

const props = defineProps<Props>()

const classes = computed(() =>
  cn(
    'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
    CHANGE_COLORS[props.type].pill,
    props.class,
  ),
)
</script>

<template>
  <span :class="classes" :data-change-type="type">{{ type }}</span>
</template>
