<script setup lang="ts">
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
import type { Watchlist } from '@/api/watchlists'

interface Props {
  open: boolean
  watchlist: Watchlist | null
  pending: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
}>()

const open = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
})
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent class="max-w-md">
      <div data-testid="remove-watchlist-dialog" />
      <DialogHeader>
        <DialogTitle>Remove watchlist</DialogTitle>
        <DialogDescription>
          <template v-if="watchlist">
            Remove <span class="font-medium">{{ watchlist.name }}</span> from your watchlist?
          </template>
          <template v-else>Remove this watchlist?</template>
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="outline" data-testid="cancel-remove" @click="open = false">Cancel</Button>
        <Button
          data-testid="confirm-remove"
          :disabled="pending"
          class="bg-red-600 text-white hover:bg-red-700"
          @click="emit('confirm')"
        >
          {{ pending ? 'Removing…' : 'Remove' }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
