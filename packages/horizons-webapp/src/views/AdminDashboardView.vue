<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="mx-auto max-w-6xl px-6 py-10">
    <div class="mb-8">
      <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Operator dashboard</h1>
      <p class="mt-1 text-sm text-slate-500">
        Manage client subscriptions and inspect the audit trail.
      </p>
    </div>
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <RouterLink
        :to="{ name: 'admin-clients' }"
        class="block rounded-md border border-slate-200 bg-white p-6 transition hover:border-slate-300 hover:shadow-sm"
        data-testid="dashboard-clients-link"
      >
        <h2 class="text-base font-semibold text-slate-900">Clients</h2>
        <p class="mt-1 text-sm text-slate-500">
          List paying clients, edit subscriptions, enter a support view.
        </p>
      </RouterLink>
      <RouterLink
        :to="{ name: 'admin-audit' }"
        class="block rounded-md border border-slate-200 bg-white p-6 transition hover:border-slate-300 hover:shadow-sm"
        data-testid="dashboard-audit-link"
      >
        <h2 class="text-base font-semibold text-slate-900">Audit log</h2>
        <p class="mt-1 text-sm text-slate-500">
          Browse admin actions and impersonation events.
        </p>
      </RouterLink>
    </div>
  </section>
</template>
