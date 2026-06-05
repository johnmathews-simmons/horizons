<script setup lang="ts">
import { computed, ref } from 'vue'
import { useChangeEvents } from '@/composables/useChangeEvents'
import { confidenceTier } from '@/constants/confidence'
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

const filteredItems = computed<DiscoveryItem[]>(() =>
  allItems.value.filter((item) => {
    if (!showMoved.value && item.change_type === 'MOVED') return false
    if (!showBelowThreshold.value && confidenceTier(item.alignment_confidence) === 'low')
      return false
    return true
  }),
)

const isInitialLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(() => !isInitialLoading.value && filteredItems.value.length === 0)

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
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <nav class="flex gap-2 text-sm">
          <RouterLink to="/" class="text-slate-600 hover:text-slate-900">Home</RouterLink>
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

      <ul v-else class="divide-y divide-slate-200 overflow-hidden rounded-md border border-slate-200 bg-white">
        <li v-for="item in filteredItems" :key="item.id" data-testid="change-row">
          <RouterLink
            :to="{ name: 'change-detail', params: { id: String(item.id) } }"
            class="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50"
          >
            <ChangeTypePill :type="item.change_type" />
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium text-slate-900">{{ pathDisplay(item) }}</div>
              <div class="mt-0.5 text-xs text-slate-500">
                {{ item.jurisdiction }} · {{ item.sector }} · {{ formatRelative(item.detected_at) }}
              </div>
            </div>
            <ConfidenceBadge :value="item.alignment_confidence" />
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
