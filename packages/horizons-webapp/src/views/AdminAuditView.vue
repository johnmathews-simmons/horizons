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
import { useAdminAuditQuery, type ListAuditParams } from '@/composables/useAdmin'
import type { AdminAuditMode } from '@/api/admin'

const DEFAULT_LIMIT = 100

function defaultSinceIso(): string {
  // now() - 24h, frozen at query construction. Re-typing in the field
  // updates `params.since`; clicking Reset re-anchors to now-24h.
  return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
}

function isoToInputLocal(iso: string): string {
  // Convert ISO to the `YYYY-MM-DDTHH:mm` shape that `<input type="datetime-local">`
  // expects. We trim seconds and TZ — datetime-local has no TZ; we re-attach Z on submit.
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

const sinceLocal = ref(isoToInputLocal(defaultSinceIso()))
const adminIdFilter = ref('')
const targetIdFilter = ref('')
const actionFilter = ref<AdminAuditMode | ''>('')

const params = ref<ListAuditParams>({
  since: defaultSinceIso(),
  limit: DEFAULT_LIMIT,
})

function applyFilters(): void {
  // Treat the datetime-local value as UTC (the input has no TZ; we already
  // populated it from UTC components above).
  const sinceIso = `${sinceLocal.value}:00Z`
  const next: ListAuditParams = { since: sinceIso, limit: DEFAULT_LIMIT }
  if (adminIdFilter.value.trim()) next.admin_id = adminIdFilter.value.trim()
  if (targetIdFilter.value.trim()) next.target_user_id = targetIdFilter.value.trim()
  if (actionFilter.value) next.action = actionFilter.value
  params.value = next
}

function resetFilters(): void {
  sinceLocal.value = isoToInputLocal(defaultSinceIso())
  adminIdFilter.value = ''
  targetIdFilter.value = ''
  actionFilter.value = ''
  params.value = { since: defaultSinceIso(), limit: DEFAULT_LIMIT }
}

const query = useAdminAuditQuery(params)

const rows = computed(() => query.data.value?.rows ?? [])
const count = computed(() => query.data.value?.count ?? 0)
const effectiveSince = computed(() => query.data.value?.since ?? params.value.since ?? '')
const isInitialLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(
  () => !isInitialLoading.value && !hasError.value && rows.value.length === 0,
)

function formatTimestamp(iso: string): string {
  if (!iso) return ''
  return new Date(iso).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

function modeLabel(mode: AdminAuditMode): string {
  return mode === 'impersonation' ? 'Impersonation' : 'Operator'
}
</script>

<template>
  <section class="mx-auto max-w-6xl px-6 py-10">
    <div class="mb-6 flex items-end justify-between">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Audit log</h1>
        <p class="mt-1 text-sm text-slate-500">
          Admin actions and impersonation events. Read-only and append-only.
        </p>
      </div>
      <div class="text-xs text-slate-500" data-testid="audit-summary">
        Showing {{ count }} row{{ count === 1 ? '' : 's' }} since
        <span data-testid="audit-effective-since">{{ effectiveSince }}</span>
      </div>
    </div>

    <div class="mb-6 rounded-md border border-slate-200 bg-white p-4">
      <div class="flex flex-wrap items-end gap-3">
        <label class="flex flex-col text-xs text-slate-600">
          Since (UTC)
          <input
            v-model="sinceLocal"
            data-testid="filter-since"
            type="datetime-local"
            class="mt-1 rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-slate-400 focus:outline-none"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          Admin ID
          <input
            v-model="adminIdFilter"
            data-testid="filter-admin-id"
            type="text"
            class="mt-1 w-64 rounded-md border border-slate-200 px-2 py-1.5 font-mono text-xs focus:border-slate-400 focus:outline-none"
            placeholder="optional UUID"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          Target user ID
          <input
            v-model="targetIdFilter"
            data-testid="filter-target-id"
            type="text"
            class="mt-1 w-64 rounded-md border border-slate-200 px-2 py-1.5 font-mono text-xs focus:border-slate-400 focus:outline-none"
            placeholder="optional UUID"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          Mode
          <select
            v-model="actionFilter"
            data-testid="filter-action"
            class="mt-1 rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-slate-400 focus:outline-none"
          >
            <option value="">All</option>
            <option value="operator">Operator</option>
            <option value="impersonation">Impersonation</option>
          </select>
        </label>
        <Button data-testid="filter-apply" @click="applyFilters">Apply</Button>
        <Button variant="outline" data-testid="filter-reset" @click="resetFilters">Reset</Button>
      </div>
    </div>

    <div
      v-if="isInitialLoading"
      data-testid="loading-state"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      Loading audit log…
    </div>

    <div
      v-else-if="hasError"
      role="alert"
      data-testid="error-state"
      class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
    >
      Could not load the audit log.
    </div>

    <div
      v-else-if="isEmpty"
      data-testid="empty-state"
      class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
    >
      No audit rows match these filters.
    </div>

    <Table v-else data-testid="audit-table">
      <TableHeader>
        <TableRow>
          <TableHead>Timestamp</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead>Admin ID</TableHead>
          <TableHead>Target user ID</TableHead>
          <TableHead>Reason</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow
          v-for="row in rows"
          :key="row.id"
          :data-testid="`audit-row-${row.id}`"
          :data-mode="row.mode"
          :class="
            row.mode === 'impersonation'
              ? 'bg-amber-50/60'
              : ''
          "
        >
          <TableCell class="font-mono text-xs">{{ formatTimestamp(row.granted_at) }}</TableCell>
          <TableCell>
            <span
              :data-testid="`audit-mode-${row.id}`"
              class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              :class="
                row.mode === 'impersonation'
                  ? 'bg-amber-200 text-amber-900'
                  : 'bg-slate-200 text-slate-800'
              "
            >
              {{ modeLabel(row.mode) }}
            </span>
          </TableCell>
          <TableCell class="font-mono text-xs text-slate-700">{{ row.admin_id }}</TableCell>
          <TableCell class="font-mono text-xs text-slate-700">
            {{ row.target_user_id ?? '—' }}
          </TableCell>
          <TableCell class="text-xs text-slate-600">{{ row.reason ?? '' }}</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  </section>
</template>
