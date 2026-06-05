<script setup lang="ts">
import { computed } from 'vue'
import { DialogDescription, type DialogDescriptionProps, useForwardProps } from 'reka-ui'
import { cn } from '@/lib/utils'

interface Props extends DialogDescriptionProps {
  class?: string
}
const props = defineProps<Props>()
const delegated = computed(() => {
  const { class: _omit, ...rest } = props
  void _omit
  return rest
})
const forwarded = useForwardProps(delegated)
const classes = computed(() => cn('text-sm text-slate-500', props.class))
</script>

<template>
  <DialogDescription v-bind="forwarded" :class="classes">
    <slot />
  </DialogDescription>
</template>
