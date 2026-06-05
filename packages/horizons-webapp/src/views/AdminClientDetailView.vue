<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { useRouter } from 'vue-router'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import {
  useAdminScopedDocumentsQuery,
  useAdminSubscriptionsQuery,
  usePatchSubscriptionMutation,
} from '@/composables/useAdmin'
import type {
  AdminScopeOut,
  AdminScopePair,
  AdminSubscriptionOut,
} from '@/api/admin'
import ScopeRemovalConfirmDialog from '@/components/admin/ScopeRemovalConfirmDialog.vue'

const props = defineProps<{ id: string }>()
const idRef = toRef(props, 'id')

const auth = useAuthStore()
const router = useRouter()
const toast = useToast()

const subscriptionsQuery = useAdminSubscriptionsQuery(idRef)
const documentsQuery = useAdminScopedDocumentsQuery()
const patchMutation = usePatchSubscriptionMutation()

const subscriptions = computed<AdminSubscriptionOut[]>(
  () => subscriptionsQuery.data.value?.subscriptions ?? [],
)
const activeSubscription = computed<AdminSubscriptionOut | null>(() => {
  return subscriptions.value.find((s) => s.valid_to === null) ?? subscriptions.value[0] ?? null
})
const activeScopes = computed<AdminScopeOut[]>(() => {
  const s = activeSubscription.value
  if (!s) return []
  return s.scopes.filter((scope) => scope.valid_to === null)
})

const newJurisdiction = ref('')
const newSector = ref('')
const addError = ref<string | null>(null)

const removeQueue = ref<AdminScopePair[]>([])
const removeOpen = ref(false)

async function onAdd(): Promise<void> {
  addError.value = null
  const jurisdiction = newJurisdiction.value.trim()
  const sector = newSector.value.trim()
  if (!jurisdiction || !sector) {
    addError.value = 'Both jurisdiction and sector are required.'
    return
  }
  if (
    activeScopes.value.some(
      (s) =>
        s.jurisdiction.toLowerCase() === jurisdiction.toLowerCase() &&
        s.sector.toLowerCase() === sector.toLowerCase(),
    )
  ) {
    addError.value = 'That scope pair is already active.'
    return
  }
  const sub = activeSubscription.value
  if (!sub) {
    addError.value = 'This client has no active subscription.'
    return
  }
  try {
    await patchMutation.mutateAsync({
      subscriptionId: sub.id,
      userId: props.id,
      body: { add_scopes: [{ jurisdiction, sector }] },
    })
    newJurisdiction.value = ''
    newSector.value = ''
    toast.success('Scope added')
  } catch {
    toast.error('Could not add scope')
  }
}

function askRemove(scope: AdminScopeOut): void {
  removeQueue.value = [{ jurisdiction: scope.jurisdiction, sector: scope.sector }]
  removeOpen.value = true
}

async function confirmRemove(): Promise<void> {
  const sub = activeSubscription.value
  if (!sub || removeQueue.value.length === 0) {
    removeOpen.value = false
    return
  }
  try {
    const result = await patchMutation.mutateAsync({
      subscriptionId: sub.id,
      userId: props.id,
      body: { remove_scopes: removeQueue.value },
    })
    removeOpen.value = false
    removeQueue.value = []
    const hidden = result.watchlists_soft_hidden
    if (hidden > 0) {
      toast.success(
        'Scope removed',
        `${hidden} watchlist${hidden === 1 ? '' : 's'} soft-hidden.`,
      )
    } else {
      toast.success('Scope removed')
    }
  } catch {
    toast.error('Could not remove scope')
  }
}

const supportPending = ref(false)
async function onEnterSupportView(): Promise<void> {
  supportPending.value = true
  try {
    await auth.enterSupportView(props.id)
    await router.push({ name: 'home' })
  } catch {
    // [[adversary class 5]] — mint failure leaves the SPA on this page,
    // does NOT enter support view. Toast the failure and stay put.
    toast.error('Could not enter support view', 'No impersonation token was minted.')
  } finally {
    supportPending.value = false
  }
}

const documents = computed(() => documentsQuery.data.value ?? [])
const isInitialLoading = computed(() => subscriptionsQuery.isPending.value)
const hasError = computed(() => subscriptionsQuery.isError.value)
</script>

<template>
  <section class="mx-auto max-w-6xl px-6 py-10">
    <div class="mb-6">
      <RouterLink
        :to="{ name: 'admin-clients' }"
        class="text-xs text-slate-500 hover:text-slate-700"
        data-testid="back-to-clients"
      >
        ← Back to clients
      </RouterLink>
      <h1 class="mt-2 text-2xl font-semibold tracking-tight text-slate-900">
        Client <span class="font-mono text-base text-slate-500">{{ id }}</span>
      </h1>
    </div>

    <div
      v-if="isInitialLoading"
      data-testid="loading-state"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      Loading client…
    </div>

    <div
      v-else-if="hasError"
      role="alert"
      data-testid="error-state"
      class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
    >
      Could not load this client.
    </div>

    <div v-else class="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div class="lg:col-span-2 space-y-6">
        <div class="rounded-md border border-slate-200 bg-white p-6">
          <div class="mb-4 flex items-end justify-between">
            <div>
              <h2 class="text-base font-semibold text-slate-900">Active subscription</h2>
              <p class="mt-1 text-xs text-slate-500">
                Scopes the client can read corpus data within.
              </p>
            </div>
            <div
              v-if="activeSubscription"
              class="text-xs text-slate-500"
              data-testid="subscription-id"
            >
              <span class="font-mono">{{ activeSubscription.id }}</span>
            </div>
          </div>

          <div
            v-if="!activeSubscription"
            data-testid="no-subscription"
            class="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600"
          >
            This client has no active subscription yet.
          </div>

          <Table v-else data-testid="scopes-table">
            <TableHeader>
              <TableRow>
                <TableHead>Jurisdiction</TableHead>
                <TableHead>Sector</TableHead>
                <TableHead class="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow
                v-for="scope in activeScopes"
                :key="`${scope.jurisdiction}-${scope.sector}`"
                :data-testid="`scope-row-${scope.jurisdiction}-${scope.sector}`"
              >
                <TableCell class="font-mono text-xs">{{ scope.jurisdiction }}</TableCell>
                <TableCell class="font-mono text-xs">{{ scope.sector }}</TableCell>
                <TableCell class="text-right">
                  <Button
                    variant="outline"
                    size="sm"
                    :data-testid="`remove-scope-${scope.jurisdiction}-${scope.sector}`"
                    :disabled="patchMutation.isPending.value"
                    @click="askRemove(scope)"
                  >
                    Remove
                  </Button>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>

        <div class="rounded-md border border-slate-200 bg-white p-6">
          <h2 class="text-base font-semibold text-slate-900">Add scope</h2>
          <p class="mt-1 text-xs text-slate-500">
            Append a new (jurisdiction, sector) pair to the active subscription.
          </p>
          <div class="mt-4 flex flex-wrap items-end gap-3">
            <label class="flex flex-col text-xs text-slate-600">
              Jurisdiction
              <input
                v-model="newJurisdiction"
                data-testid="add-scope-jurisdiction"
                type="text"
                class="mt-1 w-32 rounded-md border border-slate-200 px-2 py-1.5 font-mono text-sm focus:border-slate-400 focus:outline-none"
                placeholder="e.g. GB"
              />
            </label>
            <label class="flex flex-col text-xs text-slate-600">
              Sector
              <input
                v-model="newSector"
                data-testid="add-scope-sector"
                type="text"
                class="mt-1 w-40 rounded-md border border-slate-200 px-2 py-1.5 font-mono text-sm focus:border-slate-400 focus:outline-none"
                placeholder="e.g. banking"
              />
            </label>
            <Button
              data-testid="add-scope-submit"
              :disabled="!activeSubscription || patchMutation.isPending.value"
              @click="onAdd"
            >
              Add
            </Button>
          </div>
          <p
            v-if="addError"
            data-testid="add-scope-error"
            class="mt-2 text-xs text-red-700"
            role="alert"
          >
            {{ addError }}
          </p>
        </div>
      </div>

      <aside class="rounded-md border border-slate-200 bg-white p-6">
        <h2 class="text-base font-semibold text-slate-900">Support</h2>
        <p class="mt-1 text-xs text-slate-500">
          Enter the client's view to reproduce a support ticket. An amber banner will mark every
          page; an audit row records the entry.
        </p>
        <Button
          class="mt-4 w-full"
          data-testid="enter-support-view"
          :disabled="supportPending"
          @click="onEnterSupportView"
        >
          {{ supportPending ? 'Entering…' : 'Enter support view' }}
        </Button>
      </aside>
    </div>

    <ScopeRemovalConfirmDialog
      v-model:open="removeOpen"
      :removing-scopes="removeQueue"
      :documents="documents"
      :pending="patchMutation.isPending.value"
      @confirm="confirmRemove"
    />
  </section>
</template>
