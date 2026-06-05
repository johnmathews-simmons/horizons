<script setup lang="ts">
import { computed } from 'vue'
import { DialogTitle, type DialogTitleProps, useForwardProps } from 'reka-ui'
import { cn } from '@/lib/utils'

interface Props extends DialogTitleProps {
  class?: string
}
const props = defineProps<Props>()
const delegated = computed(() => {
  const { class: _omit, ...rest } = props
  void _omit
  return rest
})
const forwarded = useForwardProps(delegated)
const classes = computed(() => cn('text-lg font-semibold text-slate-900', props.class))
</script>

<template>
  <DialogTitle v-bind="forwarded" :class="classes">
    <slot />
  </DialogTitle>
</template>
