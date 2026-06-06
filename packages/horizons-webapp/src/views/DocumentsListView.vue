<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { listDocuments, type DocumentItem, type DocumentPage } from '@/api/documents'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui/button'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

async function onSignOut(): Promise<void> {
  await auth.logout()
  await router.push({ name: 'login' })
}

const PAGE_SIZE = 50

const search = ref<string>(typeof route.query.search === 'string' ? route.query.search : '')
const jurisdiction = ref<string>(
  typeof route.query.jurisdiction === 'string' ? route.query.jurisdiction : '',
)
const sector = ref<string>(typeof route.query.sector === 'string' ? route.query.sector : '')

// Sync filter state back into the URL so the view is shareable / reload-safe.
watch([search, jurisdiction, sector], async () => {
  await router.replace({
    name: 'documents',
    query: {
      ...(search.value ? { search: search.value } : {}),
      ...(jurisdiction.value ? { jurisdiction: jurisdiction.value } : {}),
      ...(sector.value ? { sector: sector.value } : {}),
    },
  })
})

const queryKey = computed(() => [
  'documents-list',
  search.value,
  jurisdiction.value,
  sector.value,
])

const query = useQuery<DocumentPage>({
  queryKey,
  queryFn: () =>
    listDocuments({
      search: search.value || undefined,
      jurisdiction: jurisdiction.value || undefined,
      sector: sector.value || undefined,
      limit: PAGE_SIZE,
      offset: 0,
    }),
})

const items = computed<DocumentItem[]>(() => query.data.value?.items ?? [])
const total = computed<number>(() => query.data.value?.total ?? 0)
const isLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(() => !isLoading.value && items.value.length === 0)

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10)
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <div class="flex items-center gap-6">
          <RouterLink to="/" class="text-lg font-semibold tracking-tight text-slate-900">
            Horizons
          </RouterLink>
          <nav class="flex gap-4 text-sm">
            <RouterLink to="/changes" class="text-slate-600 hover:text-slate-900">
              Changes
            </RouterLink>
            <RouterLink
              to="/documents"
              class="text-slate-900 font-medium"
              data-testid="nav-documents"
            >
              Documents
            </RouterLink>
            <RouterLink to="/watchlists" class="text-slate-600 hover:text-slate-900">
              Watchlists
            </RouterLink>
          </nav>
        </div>
        <div class="flex items-center gap-3 text-sm text-slate-600">
          <span v-if="auth.principal" data-testid="user-email">{{ auth.principal.email }}</span>
          <Button variant="outline" size="sm" data-testid="sign-out" @click="onSignOut">
            Sign out
          </Button>
        </div>
      </div>
    </header>

    <section class="mx-auto max-w-6xl px-6 py-10">
      <div class="mb-6">
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Documents</h1>
        <p class="mt-1 text-sm text-slate-500">
          Browse the documents in your subscription scope. Open one to see its
          clause structure.
        </p>
      </div>

      <div
        class="mb-4 flex flex-wrap items-end gap-3 rounded-md border border-slate-200 bg-white p-3"
      >
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

      <ul
        v-else
        role="list"
        class="overflow-hidden rounded-md border border-slate-200 bg-white"
      >
        <li
          v-for="item in items"
          :key="item.id"
          data-testid="document-row"
          class="border-b border-slate-200 last:border-b-0"
        >
          <RouterLink
            :to="{ name: 'document-detail', params: { id: item.id } }"
            class="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50"
          >
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-medium text-slate-900">
                {{ item.title }}
              </div>
              <div class="mt-0.5 text-xs text-slate-500">
                {{ item.jurisdiction }} · {{ item.sector }} · added
                {{ formatDate(item.created_at) }}
              </div>
            </div>
            <span class="text-xs text-slate-400">{{ item.lawstronaut_document_id }}</span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </main>
</template>
