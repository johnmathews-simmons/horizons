<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useMeOverview } from '@/composables/useMeOverview'
import AppNavBar from '@/components/AppNavBar.vue'
import JurisdictionCard from '@/components/overview/JurisdictionCard.vue'
import SectorCard from '@/components/overview/SectorCard.vue'

const router = useRouter()
const overview = useMeOverview()

function goToJurisdiction(code: string): void {
  router.push({ name: 'changes', query: { jurisdiction: code } })
}

function goToSector(code: string): void {
  router.push({ name: 'changes', query: { sector: code } })
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <AppNavBar />

    <section class="mx-auto max-w-6xl px-6 py-10">
      <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Your corpus</h1>
      <p class="mt-2 text-slate-600">
        An overview of the regulatory documents your subscription covers.
      </p>

      <div v-if="overview.isPending.value" class="mt-8 text-slate-500" data-testid="overview-loading">
        Loading…
      </div>
      <div v-else-if="overview.isError.value" class="mt-8 rounded-md border border-red-200 bg-red-50 p-4 text-red-700" data-testid="overview-error">
        Couldn't load your overview. Try refreshing.
      </div>
      <template v-else-if="overview.data.value">
        <!-- Summary row -->
        <div class="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="overview-summary">
          <template v-if="overview.data.value.is_admin">
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Access</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">Full corpus</div>
              <div class="mt-1 text-sm text-slate-600">
                {{ overview.data.value.totals.documents }} documents across
                {{ overview.data.value.totals.jurisdictions }} jurisdictions and
                {{ overview.data.value.totals.sectors }} sectors
              </div>
            </div>
          </template>
          <template v-else>
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Jurisdictions</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">
                {{ overview.data.value.totals.subscribed_jurisdictions }} /
                {{ overview.data.value.totals.jurisdictions }}
              </div>
            </div>
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Sectors</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">
                {{ overview.data.value.totals.subscribed_sectors }} /
                {{ overview.data.value.totals.sectors }}
              </div>
            </div>
          </template>
        </div>

        <!-- Jurisdictions -->
        <h2 class="mt-10 text-lg font-semibold tracking-tight text-slate-900">Jurisdictions</h2>
        <div class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <JurisdictionCard
            v-for="j in overview.data.value.jurisdictions"
            :key="j.code"
            :code="j.code"
            :document-count="j.document_count"
            :subscribed="j.subscribed"
            @select="goToJurisdiction"
          />
        </div>

        <!-- Sectors -->
        <h2 class="mt-10 text-lg font-semibold tracking-tight text-slate-900">Sectors</h2>
        <div class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
          <SectorCard
            v-for="s in overview.data.value.sectors"
            :key="s.code"
            :code="s.code"
            :document-count="s.document_count"
            :subscribed="s.subscribed"
            @select="goToSector"
          />
        </div>
      </template>
    </section>
  </main>
</template>
