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
import { ToastViewport } from '@/components/ui/toast'
import AddWatchlistDialog from '@/components/watchlists/AddWatchlistDialog.vue'
import RemoveWatchlistDialog from '@/components/watchlists/RemoveWatchlistDialog.vue'
import {
  useRemoveWatchlistMutation,
  useWatchlistsQuery,
} from '@/composables/useWatchlists'
import { useToast } from '@/composables/useToast'
import type { Watchlist } from '@/api/watchlists'

const query = useWatchlistsQuery()
const removeMutation = useRemoveWatchlistMutation()
const toast = useToast()

const addOpen = ref(false)
const pendingRemoval = ref<Watchlist | null>(null)
const removeOpen = computed({
  get: () => pendingRemoval.value !== null,
  set: (value) => {
    if (!value) pendingRemoval.value = null
  },
})

const watchlists = computed<Watchlist[]>(() => query.data.value ?? [])
const isInitialLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(
  () => !isInitialLoading.value && !hasError.value && watchlists.value.length === 0,
)

function askRemove(watchlist: Watchlist): void {
  pendingRemoval.value = watchlist
}

async function confirmRemove(): Promise<void> {
  const target = pendingRemoval.value
  if (!target) return
  pendingRemoval.value = null
  try {
    await removeMutation.mutateAsync(target.id)
    toast.success('Watchlist removed')
  } catch {
    toast.error('Could not remove watchlist')
  }
}

function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

function displayName(row: Watchlist): string {
  // Prefer the user-set name; if blank, fall back to the joined document
  // title; if that's also unavailable (write-only path or older row),
  // fall back to the document ID so the row is never visually empty.
  const trimmed = row.name?.trim()
  if (trimmed) return trimmed
  if (row.document_title) return row.document_title
  return row.document_id
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <nav class="flex gap-4 text-sm">
          <RouterLink to="/" class="text-slate-600 hover:text-slate-900">Home</RouterLink>
          <RouterLink to="/changes" class="text-slate-600 hover:text-slate-900">Changes</RouterLink>
        </nav>
      </div>
    </header>

    <section class="mx-auto max-w-5xl px-6 py-10">
      <div class="mb-6 flex items-end justify-between">
        <div>
          <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Watchlists</h1>
          <p class="mt-1 text-sm text-slate-500">
            Documents monitored for clause-level changes.
          </p>
        </div>
        <Button data-testid="open-add-dialog" @click="addOpen = true">Add documents</Button>
      </div>

      <div
        v-if="isInitialLoading"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading watchlists…
      </div>

      <div
        v-else-if="hasError"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load watchlists.
      </div>

      <div
        v-else-if="isEmpty"
        data-testid="empty-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        No watchlists yet. Click <span class="font-medium">Add documents</span> to start monitoring.
      </div>

      <Table v-else data-testid="watchlist-table">
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Jurisdiction</TableHead>
            <TableHead>Sector</TableHead>
            <TableHead>Document ID</TableHead>
            <TableHead>Added</TableHead>
            <TableHead class="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="row in watchlists"
            :key="row.id"
            :data-testid="`watchlist-row-${row.id}`"
            data-row-testid="watchlist-row"
          >
            <TableCell class="font-medium">{{ displayName(row) }}</TableCell>
            <TableCell class="text-slate-700">{{ row.document_jurisdiction ?? '—' }}</TableCell>
            <TableCell class="text-slate-700">{{ row.document_sector ?? '—' }}</TableCell>
            <TableCell class="font-mono text-xs text-slate-500">{{ row.document_id }}</TableCell>
            <TableCell class="text-slate-600">{{ formatDate(row.created_at) }}</TableCell>
            <TableCell class="text-right">
              <Button
                variant="outline"
                size="sm"
                :data-testid="`remove-${row.id}`"
                @click="askRemove(row)"
              >
                Remove
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </section>

    <AddWatchlistDialog v-model:open="addOpen" :existing="watchlists" />
    <RemoveWatchlistDialog
      v-model:open="removeOpen"
      :watchlist="pendingRemoval"
      :pending="removeMutation.isPending.value"
      @confirm="confirmRemove"
    />
    <ToastViewport />
  </main>
</template>
