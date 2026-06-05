<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useScopedDocuments, type ScopedDocument } from '@/composables/useScopedDiscovery'
import { useAddWatchlistMutation } from '@/composables/useWatchlists'
import { useToast } from '@/composables/useToast'
import type { Watchlist } from '@/api/watchlists'

interface Props {
  open: boolean
  existing: Watchlist[]
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const open = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
})

const search = ref('')
const selected = ref<Set<string>>(new Set())

const { documents, isPending, isError } = useScopedDocuments()
const addMutation = useAddWatchlistMutation()
const toast = useToast()

const existingIds = computed(() => new Set(props.existing.map((w) => w.document_id)))

const filtered = computed<ScopedDocument[]>(() => {
  const query = search.value.trim().toLowerCase()
  return documents.value.filter((doc) => {
    if (existingIds.value.has(doc.document_id)) return false
    if (!query) return true
    return (
      doc.document_id.toLowerCase().includes(query) ||
      doc.jurisdiction.toLowerCase().includes(query) ||
      doc.sector.toLowerCase().includes(query)
    )
  })
})

function toggle(documentId: string): void {
  const next = new Set(selected.value)
  if (next.has(documentId)) {
    next.delete(documentId)
  } else {
    next.add(documentId)
  }
  selected.value = next
}

async function onSubmit(): Promise<void> {
  const ids = Array.from(selected.value)
  if (ids.length === 0) return
  const results = await Promise.allSettled(
    ids.map((document_id) => addMutation.mutateAsync({ document_id })),
  )
  const failed = results.filter((r) => r.status === 'rejected').length
  const added = results.length - failed
  if (added > 0) {
    toast.success(`Added ${added} watchlist${added === 1 ? '' : 's'}`)
  }
  if (failed > 0) {
    toast.error(`${failed} watchlist${failed === 1 ? '' : 's'} failed to add`)
  }
  selected.value = new Set()
  search.value = ''
  open.value = false
}

watch(open, (next) => {
  if (!next) {
    selected.value = new Set()
    search.value = ''
  }
})
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent class="max-w-xl">
      <div data-testid="add-watchlist-dialog" />
      <DialogHeader>
        <DialogTitle>Add documents to watchlist</DialogTitle>
        <DialogDescription>
          Pick one or more in-scope documents. Documents outside your subscription scope are not listed.
        </DialogDescription>
      </DialogHeader>

      <div class="space-y-3">
        <Input
          v-model="search"
          type="search"
          placeholder="Search by document ID, jurisdiction, or sector"
          data-testid="search-input"
        />

        <div
          v-if="isPending"
          data-testid="discovery-loading"
          class="rounded-md border border-slate-200 p-4 text-sm text-slate-500"
        >
          Loading in-scope documents…
        </div>

        <div
          v-else-if="isError"
          role="alert"
          data-testid="discovery-error"
          class="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          Could not load in-scope documents.
        </div>

        <div
          v-else-if="filtered.length === 0"
          data-testid="no-discovery-results"
          class="rounded-md border border-slate-200 p-4 text-sm text-slate-500"
        >
          No documents match.
        </div>

        <ul v-else role="list" class="max-h-72 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
          <li v-for="doc in filtered" :key="doc.document_id" data-testid="discovery-row" class="px-3 py-2">
            <label class="flex cursor-pointer items-center gap-3">
              <input
                type="checkbox"
                :checked="selected.has(doc.document_id)"
                :data-testid="`discovery-checkbox-${doc.document_id}`"
                class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-700"
                @change="toggle(doc.document_id)"
              />
              <div class="min-w-0 flex-1">
                <div class="truncate font-mono text-xs text-slate-700">{{ doc.document_id }}</div>
                <div class="text-xs text-slate-500">{{ doc.jurisdiction }} · {{ doc.sector }}</div>
              </div>
            </label>
          </li>
        </ul>
      </div>

      <DialogFooter>
        <Button variant="outline" data-testid="cancel-add" @click="open = false">Cancel</Button>
        <Button
          data-testid="confirm-add"
          :disabled="selected.size === 0 || addMutation.isPending.value"
          @click="onSubmit"
        >
          {{ addMutation.isPending.value ? 'Adding…' : `Add ${selected.size || ''}`.trim() }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
