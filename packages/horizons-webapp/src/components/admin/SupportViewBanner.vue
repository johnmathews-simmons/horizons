<script setup lang="ts">
/**
 * Persistent amber banner shown across the entire SPA whenever the auth
 * store holds an impersonation bearer. Mounted at the App.vue layout root
 * (NOT inside any specific view) so the operator-side deception mitigation
 * persists across every route the admin navigates during support view.
 *
 * Accessibility posture: `role="status"` + `aria-live="polite"` so screen
 * readers announce entry / exit; `bg-amber-500` + dark text gives 4.5:1
 * contrast at the chosen weight; the Exit affordance is a real focusable
 * button. See [[adversary class 3]] in the WU5.4 journal for why the
 * banner is non-dismissable and why a tab-title prefix is set alongside.
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const state = computed(() => auth.impersonationState)

function onExit(): void {
  auth.exitSupportView()
  void router.push({ name: 'admin-clients' })
}
</script>

<template>
  <div
    v-if="state"
    role="status"
    aria-live="polite"
    data-testid="support-view-banner"
    class="sticky top-0 z-50 flex w-full items-center justify-between gap-4 bg-amber-500 px-6 py-3 text-sm font-medium text-slate-950 shadow-md"
  >
    <div class="flex min-w-0 items-center gap-3">
      <span
        aria-hidden="true"
        class="inline-flex h-6 w-6 flex-none items-center justify-center rounded-full bg-amber-700 text-xs font-bold text-amber-50"
      >!</span>
      <span class="truncate">
        Support view — viewing
        <span data-testid="support-view-target" class="font-semibold">{{ state.targetEmail }}</span>
        as
        <span data-testid="support-view-admin" class="font-semibold">{{ state.originalAdminEmail }}</span>
      </span>
    </div>
    <button
      type="button"
      data-testid="support-view-exit"
      class="flex-none rounded-md border border-amber-900 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-900 transition hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-900 focus:ring-offset-2 focus:ring-offset-amber-500"
      @click="onExit"
    >
      Exit support view
    </button>
  </div>
</template>
