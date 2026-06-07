<script setup lang="ts">
import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { getClauses, type ClauseBundle } from '@/api/documents'
import ClauseOverlay from './ClauseOverlay.vue'
import type { ChangeType } from '@/constants/change-colors'

interface Props {
  documentId: string
  versionLabel: string
  seenAt: string
  showStructure: boolean
  changeMap?: Record<string, ChangeType> | null
  scrollToPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { changeMap: null, scrollToPath: null })

const query = useQuery<ClauseBundle>({
  queryKey: computed(() => ['document-clauses', props.documentId, props.versionLabel]),
  queryFn: () => getClauses(props.documentId, props.versionLabel),
  enabled: computed(() => props.versionLabel.length > 0),
})

const seenDate = computed<string>(() => props.seenAt.slice(0, 10))
</script>

<template>
  <section class="flex min-w-0 flex-col">
    <header
      data-testid="version-pane-header"
      class="mb-2 flex items-baseline gap-2 border-b border-slate-200 pb-2"
    >
      <span class="text-sm font-semibold text-slate-900">{{ versionLabel }}</span>
      <span class="text-xs text-slate-500">· seen {{ seenDate }}</span>
    </header>

    <div
      v-if="query.isPending.value"
      data-testid="version-pane-loading"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      Loading clauses…
    </div>

    <div
      v-else-if="query.isError.value"
      role="alert"
      data-testid="version-pane-error"
      class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
    >
      Could not load the clauses for this version.
    </div>

    <ClauseOverlay
      v-else-if="query.data.value"
      :clauses="query.data.value.clauses"
      :show-structure="showStructure"
      :change-map="changeMap"
      :scroll-to-path="scrollToPath"
    />
  </section>
</template>
