<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useDifferential } from '@/composables/useDifferential'
import { Button } from '@/components/ui/button'
import { ConfidenceBadge } from '@/components/ui/confidence-badge'
import { ChangeTypePill } from '@/components/ui/change-type-pill'
import { DiffView } from '@/components/ui/diff-view'

const route = useRoute()

const eventId = computed(() => Number(route.params.id))
const query = useDifferential(eventId)

const mode = ref<'side-by-side' | 'unified'>('side-by-side')

const event = computed(() => query.data.value ?? null)

const pathDisplay = computed(() => {
  const e = event.value
  if (!e) return ''
  if (e.before_path && e.after_path && e.before_path !== e.after_path) {
    return `${e.before_path} → ${e.after_path}`
  }
  return e.after_path ?? e.before_path ?? '—'
})

function isNotFound(): boolean {
  const err = query.error.value as { response?: { status?: number } } | null
  return err?.response?.status === 404
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <RouterLink
          to="/changes"
          class="text-sm text-slate-600 hover:text-slate-900"
          data-testid="back-to-changes"
        >
          ← All changes
        </RouterLink>
      </div>
    </header>

    <section class="mx-auto max-w-5xl px-6 py-10">
      <div
        v-if="query.isPending.value"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading clause diff…
      </div>

      <div
        v-else-if="query.isError.value && isNotFound()"
        data-testid="not-found-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-700"
      >
        This change event isn't in your subscription scope.
      </div>

      <div
        v-else-if="query.isError.value"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load the clause diff. Please try again.
      </div>

      <template v-else-if="event">
        <div class="mb-6 flex flex-wrap items-center gap-3">
          <ChangeTypePill :type="event.change_type" />
          <span data-testid="path-display" class="font-medium text-slate-900">
            {{ pathDisplay }}
          </span>
          <span class="text-sm text-slate-500">
            {{ event.jurisdiction }} · {{ event.sector }}
          </span>
          <span class="ml-auto">
            <ConfidenceBadge :value="event.alignment_confidence" />
          </span>
        </div>

        <div class="mb-4 flex items-center gap-2">
          <Button
            :variant="mode === 'side-by-side' ? 'default' : 'outline'"
            size="sm"
            data-testid="mode-side-by-side"
            @click="mode = 'side-by-side'"
          >
            Side-by-side
          </Button>
          <Button
            :variant="mode === 'unified' ? 'default' : 'outline'"
            size="sm"
            data-testid="mode-unified"
            @click="mode = 'unified'"
          >
            Unified
          </Button>
        </div>

        <DiffView :before="event.before_text" :after="event.after_text" :mode="mode" />
      </template>
    </section>
  </main>
</template>
