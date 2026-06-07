<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { listDocuments, type DocumentItem, type DocumentPage } from '@/api/documents'
import AppNavBar from '@/components/AppNavBar.vue'
import { Button } from '@/components/ui/button'

const router = useRouter()
const route = useRoute()

const PAGE_SIZE = 25

const search = ref<string>(typeof route.query.search === 'string' ? route.query.search : '')
const jurisdiction = ref<string>(
  typeof route.query.jurisdiction === 'string' ? route.query.jurisdiction : '',
)
const sector = ref<string>(typeof route.query.sector === 'string' ? route.query.sector : '')
const offset = ref<number>(
  typeof route.query.offset === 'string' ? Math.max(0, parseInt(route.query.offset, 10) || 0) : 0,
)

// Sync filter state back into the URL so the view is shareable / reload-safe.
// Filters reset offset to 0 to avoid landing on an empty page.
watch([search, jurisdiction, sector], async () => {
  offset.value = 0
  await syncUrl()
})
watch(offset, async () => {
  await syncUrl()
})

async function syncUrl(): Promise<void> {
  await router.replace({
    name: 'documents',
    query: {
      ...(search.value ? { search: search.value } : {}),
      ...(jurisdiction.value ? { jurisdiction: jurisdiction.value } : {}),
      ...(sector.value ? { sector: sector.value } : {}),
      ...(offset.value > 0 ? { offset: String(offset.value) } : {}),
    },
  })
}

const queryKey = computed(() => [
  'documents-list',
  search.value,
  jurisdiction.value,
  sector.value,
  offset.value,
])

const query = useQuery<DocumentPage>({
  queryKey,
  queryFn: () =>
    listDocuments({
      search: search.value || undefined,
      jurisdiction: jurisdiction.value || undefined,
      sector: sector.value || undefined,
      limit: PAGE_SIZE,
      offset: offset.value,
    }),
})

const items = computed<DocumentItem[]>(() => query.data.value?.items ?? [])
const total = computed<number>(() => query.data.value?.total ?? 0)
const isLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(() => !isLoading.value && items.value.length === 0)

const pageStart = computed(() => (total.value === 0 ? 0 : offset.value + 1))
const pageEnd = computed(() => Math.min(offset.value + PAGE_SIZE, total.value))
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const currentPage = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
const prevDisabled = computed(() => offset.value === 0)
const nextDisabled = computed(() => offset.value + PAGE_SIZE >= total.value)

function nextPage(): void {
  if (nextDisabled.value) return
  offset.value = offset.value + PAGE_SIZE
}
function prevPage(): void {
  if (prevDisabled.value) return
  offset.value = Math.max(0, offset.value - PAGE_SIZE)
}

async function openDocument(id: string): Promise<void> {
  await router.push({ name: 'document-detail', params: { id } })
}

function fmtDate(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toISOString().slice(0, 10)
}

function fmtCount(n: number): string {
  return n === 0 ? '—' : String(n)
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <AppNavBar />

    <section class="mx-auto max-w-7xl px-6 py-10">
      <div class="mb-6">
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Documents</h1>
        <p class="mt-1 text-sm text-slate-500">
          Browse the documents in your subscription scope. Open one to see its
          clause structure and changes.
        </p>
      </div>

      <div class="mb-4 flex flex-wrap items-end gap-3 rounded-md border border-slate-200 bg-white p-3">
        <label class="flex flex-1 flex-col text-xs text-slate-600">
          <span class="mb-1">Search title</span>
          <input
            v-model="search"
            type="text"
            data-testid="filter-search"
            placeholder="e.g. employment, banking"
            class="rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          <span class="mb-1">Jurisdiction</span>
          <input
            v-model="jurisdiction"
            type="text"
            data-testid="filter-jurisdiction"
            placeholder="UK, EU, IE…"
            class="w-32 rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          <span class="mb-1">Sector</span>
          <input
            v-model="sector"
            type="text"
            data-testid="filter-sector"
            placeholder="BANKING, employment…"
            class="w-40 rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <span data-testid="documents-total" class="ml-auto text-xs text-slate-500"
          >{{ total }} document{{ total === 1 ? '' : 's' }}</span
        >
      </div>

      <div
        v-if="isLoading"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading documents…
      </div>

      <div
        v-else-if="hasError"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load documents. Please try again.
      </div>

      <div
        v-else-if="isEmpty"
        data-testid="empty-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        No documents match these filters.
      </div>

      <table
        v-else
        class="w-full overflow-hidden rounded-md border border-slate-200 bg-white text-sm"
      >
        <thead class="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" class="px-4 py-2 font-semibold">Name</th>
            <th scope="col" class="px-4 py-2 text-right font-semibold">Length</th>
            <th scope="col" class="px-4 py-2 text-right font-semibold">Added</th>
            <th scope="col" class="px-4 py-2 text-right font-semibold">Removed</th>
            <th scope="col" class="px-4 py-2 text-right font-semibold">Modified</th>
            <th scope="col" class="px-4 py-2 text-right font-semibold">Moved</th>
            <th scope="col" class="px-4 py-2 font-semibold">Previous version</th>
            <th scope="col" class="px-4 py-2 font-semibold">Current version</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.id"
            data-testid="document-row"
            class="cursor-pointer border-t border-slate-200 hover:bg-slate-50"
            @click="openDocument(item.id)"
          >
            <td class="px-4 py-2">
              <RouterLink
                :to="{ name: 'document-detail', params: { id: item.id } }"
                class="font-medium text-slate-900 hover:underline"
              >
                {{ item.title }}
              </RouterLink>
              <div class="text-xs text-slate-500">{{ item.jurisdiction }} · {{ item.sector }}</div>
            </td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ item.clause_count }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.added) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.removed) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.modified) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.moved) }}</td>
            <td class="px-4 py-2 tabular-nums text-slate-700">{{ fmtDate(item.previous_version_at) }}</td>
            <td class="px-4 py-2 tabular-nums text-slate-700">{{ fmtDate(item.current_version_at) }}</td>
          </tr>
        </tbody>
      </table>

      <div
        v-if="!isLoading && !isEmpty && total > 0"
        class="mt-4 flex items-center justify-between text-xs text-slate-600"
      >
        <span data-testid="page-range">Showing {{ pageStart }}–{{ pageEnd }} of {{ total }}</span>
        <div class="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            data-testid="page-prev"
            :disabled="prevDisabled"
            @click="prevPage"
          >
            ‹ Prev
          </Button>
          <span data-testid="page-indicator">Page {{ currentPage }} of {{ totalPages }}</span>
          <Button
            variant="outline"
            size="sm"
            data-testid="page-next"
            :disabled="nextDisabled"
            @click="nextPage"
          >
            Next ›
          </Button>
        </div>
      </div>
    </section>
  </main>
</template>
