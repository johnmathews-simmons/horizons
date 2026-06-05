<script setup lang="ts">
import { useToast } from '@/composables/useToast'

const { toasts, dismiss } = useToast()
</script>

<template>
  <div
    aria-live="polite"
    aria-atomic="true"
    data-testid="toast-viewport"
    class="pointer-events-none fixed right-4 top-4 z-50 flex w-full max-w-sm flex-col gap-2"
  >
    <div
      v-for="toast in toasts"
      :key="toast.id"
      :data-testid="`toast-${toast.variant}`"
      :class="[
        'pointer-events-auto flex items-start gap-3 rounded-md border p-4 text-sm shadow-md',
        toast.variant === 'success'
          ? 'border-green-200 bg-green-50 text-green-900'
          : 'border-red-200 bg-red-50 text-red-900',
      ]"
      role="alert"
    >
      <div class="flex-1">
        <div class="font-medium">{{ toast.title }}</div>
        <div v-if="toast.description" class="mt-0.5 text-xs opacity-80">{{ toast.description }}</div>
      </div>
      <button
        type="button"
        :aria-label="`dismiss ${toast.title}`"
        class="rounded p-0.5 opacity-60 hover:opacity-100"
        @click="dismiss(toast.id)"
      >
        ×
      </button>
    </div>
  </div>
</template>
