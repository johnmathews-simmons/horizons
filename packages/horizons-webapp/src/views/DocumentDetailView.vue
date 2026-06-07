<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { getDocument, type DocumentDetail, type DocumentVersion } from '@/api/documents'
import { fetchDiscovery, type DiscoveryItem, type DiscoveryPage } from '@/api/changes'
import { Button } from '@/components/ui/button'
import VersionPane from '@/components/documents/VersionPane.vue'
import AppNavBar from '@/components/AppNavBar.vue'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

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

const hasTwoVersions = computed(() => sortedVersions.value.length >= 2)

const changesQuery = useQuery<DiscoveryPage>({
  queryKey: computed(() => ['document-changes', documentId.value]),
  queryFn: () =>
    fetchDiscovery({ scope: 'document', document_id: documentId.value, limit: 200 }),
  enabled: hasTwoVersions,
})

const changeItems = computed<DiscoveryItem[]>(() => changesQuery.data.value?.items ?? [])

const beforeMap = computed<Record<string, ChangeType>>(() => {
  const m: Record<string, ChangeType> = {}
  for (const c of changeItems.value) {
    if (
      c.before_path &&
      (c.change_type === 'REMOVED' ||
        c.change_type === 'MODIFIED' ||
        c.change_type === 'MOVED')
    ) {
      m[c.before_path] = c.change_type
    }
  }
  return m
})

const afterMap = computed<Record<string, ChangeType>>(() => {
  const m: Record<string, ChangeType> = {}
  for (const c of changeItems.value) {
    if (
      c.after_path &&
      (c.change_type === 'ADDED' ||
        c.change_type === 'MODIFIED' ||
        c.change_type === 'MOVED')
    ) {
      m[c.after_path] = c.change_type
    }
  }
  return m
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

const lonePaneVersion = computed<DocumentVersion | null>(() => {
  const v = sortedVersions.value
  return v.length === 0 ? null : v[v.length - 1]!
})

const legendTypes: ChangeType[] = ['ADDED', 'REMOVED', 'MODIFIED', 'MOVED']
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
            :scroll-to-path="afterPath ?? beforePath"
          />
        </div>

        <!-- Multi-version: legend + two equal-width panes side-by-side. -->
        <div v-else data-testid="side-by-side">
          <div
            v-if="hasTwoVersions && changeItems.length > 0"
            data-testid="diff-legend"
            class="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-600"
          >
            <span class="mr-1">Changes:</span>
            <span
              v-for="t in legendTypes"
              :key="t"
              class="inline-flex items-center rounded px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset"
              :class="CHANGE_COLORS[t].pill"
            >{{ CHANGE_COLORS[t].label }}</span>
          </div>

          <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
            <VersionPane
              :document-id="documentId"
              :version-label="sortedVersions[0]!.version_label"
              :seen-at="sortedVersions[0]!.created_at"
              :show-structure="showStructure"
              :change-map="beforeMap"
              :scroll-to-path="beforePath"
            />
            <VersionPane
              :document-id="documentId"
              :version-label="sortedVersions[sortedVersions.length - 1]!.version_label"
              :seen-at="sortedVersions[sortedVersions.length - 1]!.created_at"
              :show-structure="showStructure"
              :change-map="afterMap"
              :scroll-to-path="afterPath"
            />
          </div>
        </div>
      </template>
    </section>
  </main>
</template>
