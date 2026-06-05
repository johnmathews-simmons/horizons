<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAdminClientsQuery, type ListAdminClientsParams } from '@/composables/useAdmin'

const PAGE_SIZE = 25

const params = ref<ListAdminClientsParams>({ limit: PAGE_SIZE, offset: 0 })
const query = useAdminClientsQuery(params)

const clients = computed(() => query.data.value?.clients ?? [])
const total = computed(() => query.data.value?.total ?? 0)
const offset = computed(() => params.value.offset ?? 0)
const limit = computed(() => params.value.limit ?? PAGE_SIZE)
const page = computed(() => Math.floor(offset.value / limit.value) + 1)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
const hasPrev = computed(() => offset.value > 0)
const hasNext = computed(() => offset.value + clients.value.length < total.value)
const isInitialLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(
  () => !isInitialLoading.value && !hasError.value && clients.value.length === 0,
)

function goPrev(): void {
  params.value = { ...params.value, offset: Math.max(0, offset.value - limit.value) }
}

function goNext(): void {
  params.value = { ...params.value, offset: offset.value + limit.value }
}

function formatDate(iso: string): string {
  return iso.slice(0, 10)
}
</script>

<template>
  <section class="mx-auto max-w-6xl px-6 py-10">
    <div class="mb-6 flex items-end justify-between">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Clients</h1>
        <p class="mt-1 text-sm text-slate-500">
          All paying clients. Open a client to edit their subscription or enter a support view.
        </p>
      </div>
      <div class="text-xs text-slate-500" data-testid="clients-page-indicator">
        Page {{ page }} of {{ totalPages }} · {{ total }} total
      </div>
    </div>

    <div
      v-if="isInitialLoading"
      data-testid="loading-state"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      Loading clients…
    </div>

    <div
      v-else-if="hasError"
      role="alert"
      data-testid="error-state"
      class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
    >
      Could not load clients.
    </div>

    <div
      v-else-if="isEmpty"
      data-testid="empty-state"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      No clients yet.
    </div>

    <Table v-else data-testid="clients-table">
      <TableHeader>
        <TableRow>
          <TableHead>Email</TableHead>
          <TableHead>Client ID</TableHead>
          <TableHead>Joined</TableHead>
          <TableHead class="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow
          v-for="row in clients"
          :key="row.user_id"
          :data-testid="`client-row-${row.user_id}`"
          data-row-testid="client-row"
        >
          <TableCell class="font-medium">{{ row.email }}</TableCell>
          <TableCell class="font-mono text-xs text-slate-500">{{ row.user_id }}</TableCell>
          <TableCell class="text-slate-600">{{ formatDate(row.created_at) }}</TableCell>
          <TableCell class="text-right">
            <RouterLink
              :to="{ name: 'admin-client-detail', params: { id: row.user_id } }"
              class="inline-flex items-center rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-900 hover:bg-slate-100"
              :data-testid="`open-${row.user_id}`"
            >
              Open
            </RouterLink>
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>

    <div class="mt-6 flex justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        data-testid="clients-prev"
        :disabled="!hasPrev || query.isFetching.value"
        @click="goPrev"
      >
        Previous
      </Button>
      <Button
        variant="outline"
        size="sm"
        data-testid="clients-next"
        :disabled="!hasNext || query.isFetching.value"
        @click="goNext"
      >
        Next
      </Button>
    </div>
  </section>
</template>
