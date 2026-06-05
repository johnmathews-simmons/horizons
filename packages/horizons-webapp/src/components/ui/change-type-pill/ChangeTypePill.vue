<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

export type ChangeType = 'ADDED' | 'REMOVED' | 'MODIFIED' | 'MOVED'

interface Props {
  type: ChangeType
  class?: string
}

const props = defineProps<Props>()

const typeClasses: Record<ChangeType, string> = {
  ADDED: 'bg-green-100 text-green-900 ring-green-300',
  REMOVED: 'bg-red-100 text-red-900 ring-red-300',
  MODIFIED: 'bg-blue-100 text-blue-900 ring-blue-300',
  MOVED: 'bg-slate-100 text-slate-700 ring-slate-300',
}

const classes = computed(() =>
  cn(
    'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
    typeClasses[props.type],
    props.class,
  ),
)
</script>

<template>
  <span :class="classes" :data-change-type="type">{{ type }}</span>
</template>
