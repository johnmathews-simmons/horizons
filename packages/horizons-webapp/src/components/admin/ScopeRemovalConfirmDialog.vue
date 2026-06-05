<script setup lang="ts">
/**
 * Confirmation modal shown BEFORE submitting a scope-removal PATCH on
 * /v1/admin/subscriptions/{id}. Lists every document the admin's
 * scoped-discovery feed reports as falling within the
 * (jurisdiction, sector) pairs being removed — those are exactly the
 * documents whose watchlists will be soft-hidden by the API's
 * `soft_hide_out_of_scope` pass.
 *
 * [[adversary class 2]] in the WU5.4 journal — without this preview the
 * admin fires the PATCH blind and can silently break a client's watchlist
 * visibility. The Cancel / X paths MUST NOT emit `confirm`; only the
 * explicit "Remove scopes" button does.
 */
import { computed } from 'vue'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import type { AdminScopePair, DiscoveryDocumentSummary } from '@/api/admin'

const props = defineProps<{
  open: boolean
  removingScopes: AdminScopePair[]
  documents: DiscoveryDocumentSummary[]
  pending?: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
}>()

const affectedDocuments = computed(() => {
  const targets = new Set(
    props.removingScopes.map((p) => `${p.jurisdiction.toLowerCase()}|${p.sector.toLowerCase()}`),
  )
  return props.documents.filter((doc) =>
    targets.has(`${doc.jurisdiction.toLowerCase()}|${doc.sector.toLowerCase()}`),
  )
})

function onOpenChange(value: boolean): void {
  // Reka-ui calls this with `false` on outside-click / Esc / X-close.
  // Forward the close but NEVER emit `confirm` here — only the explicit
  // confirm button is allowed to submit.
  emit('update:open', value)
}

function onConfirm(): void {
  emit('confirm')
}
</script>

<template>
  <Dialog :open="open" @update:open="onOpenChange">
    <DialogContent data-testid="scope-removal-confirm-dialog">
      <div data-testid="scope-removal-confirm-body">
        <DialogHeader>
          <DialogTitle>Remove subscription scopes?</DialogTitle>
          <DialogDescription>
            The following client documents fall inside the scopes you are removing. Their
            watchlists will be soft-hidden after this change.
          </DialogDescription>
        </DialogHeader>

        <div class="mt-4 space-y-3">
          <div>
            <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Scopes being removed
            </h3>
            <ul class="mt-2 space-y-1 text-sm text-slate-700">
              <li
                v-for="pair in removingScopes"
                :key="`${pair.jurisdiction}-${pair.sector}`"
                data-testid="scope-removal-pair"
                class="font-mono text-xs"
              >
                {{ pair.jurisdiction }} · {{ pair.sector }}
              </li>
            </ul>
          </div>

          <div>
            <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Affected documents ({{ affectedDocuments.length }})
            </h3>
            <p
              v-if="affectedDocuments.length === 0"
              data-testid="scope-removal-no-docs"
              class="mt-2 text-sm text-slate-500"
            >
              No documents in the discovery feed match the scopes being removed.
            </p>
            <ul
              v-else
              class="mt-2 max-h-48 space-y-1 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2 text-xs"
            >
              <li
                v-for="doc in affectedDocuments"
                :key="doc.document_id"
                :data-testid="`scope-removal-doc-${doc.document_id}`"
                class="font-mono text-slate-700"
              >
                {{ doc.document_id }}
                <span class="text-slate-500">({{ doc.jurisdiction }} · {{ doc.sector }})</span>
              </li>
            </ul>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            data-testid="scope-removal-cancel"
            :disabled="pending"
            @click="onOpenChange(false)"
          >
            Cancel
          </Button>
          <Button
            class="bg-red-600 text-white hover:bg-red-700"
            data-testid="scope-removal-confirm"
            :disabled="pending"
            @click="onConfirm"
          >
            {{ pending ? 'Removing…' : 'Remove scopes' }}
          </Button>
        </DialogFooter>
      </div>
    </DialogContent>
  </Dialog>
</template>
