<script setup lang="ts">
import { computed } from 'vue'
import {
  DialogClose,
  DialogContent,
  type DialogContentEmits,
  type DialogContentProps,
  DialogOverlay,
  DialogPortal,
  useForwardPropsEmits,
} from 'reka-ui'
import { cn } from '@/lib/utils'

interface Props extends DialogContentProps {
  class?: string
}

const props = defineProps<Props>()
const emits = defineEmits<DialogContentEmits>()

const delegated = computed(() => {
  const { class: _omit, ...rest } = props
  void _omit
  return rest
})
const forwarded = useForwardPropsEmits(delegated, emits)

const contentClasses = computed(() =>
  cn(
    'fixed left-1/2 top-1/2 z-50 grid w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 border border-slate-200 bg-white p-6 shadow-lg sm:rounded-lg',
    props.class,
  ),
)
</script>

<template>
  <DialogPortal>
    <DialogOverlay
      class="fixed inset-0 z-50 bg-slate-900/40 data-[state=open]:animate-in data-[state=closed]:animate-out"
    />
    <DialogContent v-bind="forwarded" :class="contentClasses">
      <slot />
      <DialogClose
        class="absolute right-4 top-4 rounded-md p-1 text-slate-500 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        data-testid="dialog-close"
        aria-label="Close"
      >
        <span aria-hidden="true">×</span>
      </DialogClose>
    </DialogContent>
  </DialogPortal>
</template>
