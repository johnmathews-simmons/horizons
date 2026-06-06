<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  code: string
  documentCount: number
  subscribed: boolean
}>()

const emit = defineEmits<{ select: [code: string] }>()

const title = computed(() => (props.subscribed ? '' : 'Subscribe to view'))

function onClick(): void {
  if (props.subscribed) emit('select', props.code)
}
</script>

<template>
  <button
    type="button"
    :title="title"
    :disabled="!subscribed"
    :class="[
      'flex w-full flex-col items-start rounded-md border p-4 text-left transition',
      subscribed
        ? 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 cursor-pointer'
        : 'border-slate-100 bg-slate-50 text-slate-400 cursor-not-allowed',
    ]"
    data-testid="jurisdiction-card"
    :data-code="code"
    :data-subscribed="subscribed"
    @click="onClick"
  >
    <span class="text-lg font-semibold tracking-tight">{{ code }}</span>
    <span class="mt-1 text-sm">
      {{ documentCount }} {{ documentCount === 1 ? 'document' : 'documents' }}
    </span>
    <span
      v-if="!subscribed"
      class="mt-2 inline-flex items-center rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600"
    >
      Not subscribed
    </span>
  </button>
</template>
