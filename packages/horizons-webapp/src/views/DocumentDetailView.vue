<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { getDocument, type DocumentDetail, type DocumentVersion } from '@/api/documents'
import { Button } from '@/components/ui/button'
import VersionPane from '@/components/documents/VersionPane.vue'
import AppNavBar from '@/components/AppNavBar.vue'

const route = useRoute()

const documentId = computed(() => String(route.params.id))

const docQuery = useQuery<DocumentDetail>({
  queryKey: computed(() => ['document-detail', documentId.value]),
  queryFn: () => getDocument(documentId.value),
})

const document = computed<DocumentDetail | null>(() => docQuery.data.value ?? null)

const sortedVersions = computed<DocumentVersion[]>(() => {
  const versions = document.value?.versions ?? []
  return [...versions].sort((a, b) => {
    const ad = a.effective_date ?? a.created_at
    const bd = b.effective_date ?? b.created_at
    return ad.localeCompare(bd)
  })
})

const beforePath = computed<string | null>(() => {
  const v = route.query.before
  return typeof v === 'string' && v.length > 0 ? v : null
})

const afterPath = computed<string | null>(() => {
  const v = route.query.after
  return typeof v === 'string' && v.length > 0 ? v : null
})

const showStructure = ref(false)

const isNotFound = computed<boolean>(() => {
  const err = docQuery.error.value as { response?: { status?: number } } | null
  return err?.response?.status === 404
})

// Single-pane case: which version do we show? Latest.
const lonePaneVersion = computed<DocumentVersion | null>(() => {
  const versions = sortedVersions.value
  if (versions.length === 0) return null
  return versions[versions.length - 1]!
})

// Highlight for the single-pane case: whichever of before/after is set.
// after takes precedence (matches the conceptual "current state").
const lonePaneHighlight = computed<string | null>(
  () => afterPath.value ?? beforePath.value,
)
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <AppNavBar />

    <section class="mx-auto max-w-7xl px-6 py-10">
      <div
        v-if="docQuery.isPending.value"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading document…
      </div>

      <div
        v-else-if="docQuery.isError.value && isNotFound"
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
              {{ document.jurisdiction }} · {{ document.sector }}
            </div>
          </div>
          <Button
            v-if="sortedVersions.length > 0"
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
          v-if="sortedVersions.length === 0"
          data-testid="no-versions-state"
          class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-600"
        >
          No content has been ingested for this document yet. The Horizons
          worker fetches and aligns clauses on its next scheduled poll.
        </div>

        <!-- Single-version: one full-width pane. -->
        <div v-else-if="sortedVersions.length === 1 && lonePaneVersion" class="grid grid-cols-1">
          <VersionPane
            :document-id="documentId"
            :version-label="lonePaneVersion.version_label"
            :seen-at="lonePaneVersion.created_at"
            :show-structure="showStructure"
            :highlight-path="lonePaneHighlight"
          />
        </div>

        <!-- Multi-version: two equal-width panes side-by-side, oldest left. -->
        <div
          v-else
          data-testid="side-by-side"
          class="grid grid-cols-1 gap-6 md:grid-cols-2"
        >
          <VersionPane
            :document-id="documentId"
            :version-label="sortedVersions[0]!.version_label"
            :seen-at="sortedVersions[0]!.created_at"
            :show-structure="showStructure"
            :highlight-path="beforePath"
          />
          <VersionPane
            :document-id="documentId"
            :version-label="sortedVersions[sortedVersions.length - 1]!.version_label"
            :seen-at="sortedVersions[sortedVersions.length - 1]!.created_at"
            :show-structure="showStructure"
            :highlight-path="afterPath"
          />
        </div>
      </template>
    </section>
  </main>
</template>
