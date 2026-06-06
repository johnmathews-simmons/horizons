<script setup lang="ts">
import { computed, ref } from 'vue'
import { useWindowVirtualizer, type VirtualItem } from '@tanstack/vue-virtual'
import { useChangeEvents } from '@/composables/useChangeEvents'
import { suppressBelowThreshold } from '@/constants/confidence'
import { Button } from '@/components/ui/button'
import { ConfidenceBadge } from '@/components/ui/confidence-badge'
import { ChangeTypePill } from '@/components/ui/change-type-pill'
import type { DiscoveryItem } from '@/api/changes'

const query = useChangeEvents()

const showMoved = ref(false)
const showBelowThreshold = ref(false)

const allItems = computed<DiscoveryItem[]>(
  () => query.data.value?.pages.flatMap((p) => p.items) ?? [],
)

const filteredItems = computed<DiscoveryItem[]>(() => {
  const suppress = suppressBelowThreshold()
  return allItems.value.filter((item) => {
    if (!showMoved.value && item.change_type === 'MOVED') return false
    if (!showBelowThreshold.value && item.alignment_confidence < suppress) return false
    return true
  })
})

const isInitialLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(() => !isInitialLoading.value && filteredItems.value.length === 0)

const listContainerRef = ref<HTMLElement | null>(null)

const virtualizer = useWindowVirtualizer({
  get count() {
    return filteredItems.value.length
  },
  estimateSize: () => 72,
  overscan: 8,
  get scrollMargin() {
    return listContainerRef.value?.offsetTop ?? 0
  },
})

interface VisibleRow {
  virtualItem: VirtualItem
  item: DiscoveryItem
  isLast: boolean
}

const visibleRows = computed<VisibleRow[]>(() => {
  const rows: VisibleRow[] = []
  const lastIndex = filteredItems.value.length - 1
  for (const virtualItem of virtualizer.value.getVirtualItems()) {
    const item = filteredItems.value[virtualItem.index]
    if (!item) continue
    rows.push({ virtualItem, item, isLast: virtualItem.index === lastIndex })
  }
  return rows
})
const totalSize = computed(() => virtualizer.value.getTotalSize())
const scrollMargin = computed(() => virtualizer.value.options.scrollMargin)

function formatRelative(iso: string): string {
  const detected = new Date(iso).getTime()
  const now = Date.now()
  const seconds = Math.max(0, Math.floor((now - detected) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return new Date(iso).toISOString().slice(0, 10)
}

function pathDisplay(item: DiscoveryItem): string {
  if (item.before_path && item.after_path && item.before_path !== item.after_path) {
    return `${item.before_path} → ${item.after_path}`
  }
  return item.after_path ?? item.before_path ?? '—'
}

function measureRow(el: Element | null) {
  // Skip the measurement when the element has no real layout (jsdom, hidden
  // tab, pre-paint). Feeding a 0-height back into the size cache collapses
  // the virtualiser's range and causes it to render every item.
  if (!(el instanceof HTMLElement)) return
  if (el.getBoundingClientRect().height <= 0) return
  virtualizer.value.measureElement(el)
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <nav class="flex gap-4 text-sm">
          <RouterLink to="/" class="text-slate-600 hover:text-slate-900">Home</RouterLink>
          <RouterLink
            to="/documents"
            class="text-slate-600 hover:text-slate-900"
          >
            Documents
          </RouterLink>
        </nav>
      </div>
    </header>

    <section class="mx-auto max-w-5xl px-6 py-10">
      <div class="mb-6 flex items-end justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Recent changes</h1>
          <p class="mt-1 text-sm text-slate-500">
            Clause-level change events across your subscription.
          </p>
        </div>
        <div class="flex items-center gap-3 text-xs text-slate-600">
          <label class="flex items-center gap-2">
            <input
              v-model="showMoved"
              type="checkbox"
              data-testid="toggle-moved"
              class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-700"
            />
            <span>Show MOVED</span>
          </label>
          <label class="flex items-center gap-2">
            <input
              v-model="showBelowThreshold"
              type="checkbox"
              data-testid="toggle-below-threshold"
              class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-700"
            />
            <span>Show below-threshold</span>
          </label>
        </div>
      </div>

      <div
        v-if="isInitialLoading"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading recent changes…
      </div>

      <div
        v-else-if="hasError"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load recent changes. Please try again.
      </div>

      <div
        v-else-if="isEmpty"
        data-testid="empty-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        No recent changes in your scope.
      </div>

      <ul
        v-else
        ref="listContainerRef"
        role="list"
        class="overflow-hidden rounded-md border border-slate-200 bg-white"
        :style="{ position: 'relative', height: `${totalSize}px`, width: '100%' }"
      >
        <li
          v-for="row in visibleRows"
          :key="row.item.id"
          :ref="(el) => measureRow(el as Element | null)"
          :data-index="row.virtualItem.index"
          data-testid="change-row"
          :class="row.isLast ? '' : 'border-b border-slate-200'"
          :style="{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            transform: `translateY(${row.virtualItem.start - scrollMargin}px)`,
          }"
        >
          <RouterLink
            :to="{ name: 'change-detail', params: { id: String(row.item.id) } }"
            class="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50"
          >
            <ChangeTypePill :type="row.item.change_type" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium text-slate-900">
                {{ pathDisplay(row.item) }}
              </div>
              <div class="mt-0.5 text-xs text-slate-500">
                {{ row.item.jurisdiction }} · {{ row.item.sector }} ·
                {{ formatRelative(row.item.detected_at) }}
              </div>
            </div>
            <ConfidenceBadge :value="row.item.alignment_confidence" />
          </RouterLink>
        </li>
      </ul>

      <div v-if="query.hasNextPage.value" class="mt-6 flex justify-center">
        <Button
          variant="outline"
          data-testid="load-more"
          :disabled="query.isFetchingNextPage.value"
          @click="query.fetchNextPage()"
        >
          {{ query.isFetchingNextPage.value ? 'Loading…' : 'Load more' }}
        </Button>
      </div>
    </section>
  </main>
</template>
