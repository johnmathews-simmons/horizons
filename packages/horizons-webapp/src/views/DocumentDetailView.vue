<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import {
  getClauses,
  getDocument,
  type ClauseBundle,
  type DocumentDetail,
} from '@/api/documents'
import { Button } from '@/components/ui/button'
import ClauseOverlay from '@/components/documents/ClauseOverlay.vue'

const route = useRoute()

const documentId = computed(() => String(route.params.id))
const requestedVersion = computed<string | null>(() => {
  const v = route.params.version
  if (typeof v !== 'string' || !v) return null
  return v
})

const docQuery = useQuery<DocumentDetail>({
  queryKey: computed(() => ['document-detail', documentId.value]),
  queryFn: () => getDocument(documentId.value),
})

const document = computed<DocumentDetail | null>(() => docQuery.data.value ?? null)

const activeVersionLabel = computed<string | null>(() => {
  if (requestedVersion.value !== null) return requestedVersion.value
  const versions = document.value?.versions ?? []
  if (versions.length === 0) return null
  // Pick the latest by effective_date; fall back to last in list.
  const sorted = [...versions].sort((a, b) => {
    const ad = a.effective_date ?? a.created_at
    const bd = b.effective_date ?? b.created_at
    return ad.localeCompare(bd)
  })
  return sorted[sorted.length - 1]!.version_label
})

const clausesQuery = useQuery<ClauseBundle>({
  queryKey: computed(() => [
    'document-clauses',
    documentId.value,
    activeVersionLabel.value,
  ]),
  queryFn: () => getClauses(documentId.value, activeVersionLabel.value!),
  enabled: computed(() => activeVersionLabel.value !== null),
})

const showStructure = ref(false)

function isNotFound(): boolean {
  const err = docQuery.error.value as { response?: { status?: number } } | null
  return err?.response?.status === 404
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <RouterLink
          to="/documents"
          class="text-sm text-slate-600 hover:text-slate-900"
          data-testid="back-to-documents"
        >
          ← All documents
        </RouterLink>
      </div>
    </header>

    <section class="mx-auto max-w-5xl px-6 py-10">
      <div
        v-if="docQuery.isPending.value"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading document…
      </div>

      <div
        v-else-if="docQuery.isError.value && isNotFound()"
        data-testid="not-found-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-700"
      >
        This document isn't in your subscription scope.
      </div>

      <div
        v-else-if="docQuery.isError.value"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load the document. Please try again.
      </div>

      <template v-else-if="document">
        <div class="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 data-testid="document-title" class="text-2xl font-semibold text-slate-900">
              {{ document.title }}
            </h1>
            <div class="mt-1 text-sm text-slate-500">
              {{ document.jurisdiction }} · {{ document.sector }} ·
              <span data-testid="document-version-label">version {{ activeVersionLabel }}</span>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            data-testid="toggle-structure"
            :aria-pressed="showStructure"
            @click="showStructure = !showStructure"
          >
            {{ showStructure ? 'Hide clause structure' : 'Show clause structure' }}
          </Button>
        </div>

        <div
          v-if="clausesQuery.isPending.value"
          data-testid="clauses-loading"
          class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
        >
          Loading clauses…
        </div>

        <div
          v-else-if="clausesQuery.isError.value"
          role="alert"
          data-testid="clauses-error-state"
          class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
        >
          Could not load the clauses for this version.
        </div>

        <ClauseOverlay
          v-else-if="clausesQuery.data.value"
          :clauses="clausesQuery.data.value.clauses"
          :show-structure="showStructure"
        />
      </template>
    </section>
  </main>
</template>
