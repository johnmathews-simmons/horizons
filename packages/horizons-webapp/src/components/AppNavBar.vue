<script setup lang="ts">
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui/button'

const auth = useAuthStore()
const router = useRouter()

async function onSignOut(): Promise<void> {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <header class="border-b border-slate-200 bg-white">
    <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
      <RouterLink
        to="/"
        class="text-lg font-semibold tracking-tight text-slate-900 hover:text-slate-700"
        data-testid="nav-home"
      >
        Horizons
      </RouterLink>
      <div class="flex items-center gap-3 text-sm">
        <RouterLink
          to="/changes"
          class="rounded-md px-3 py-1.5 text-slate-700 hover:bg-slate-100"
          data-testid="nav-changes"
        >
          Browse recent changes
        </RouterLink>
        <RouterLink
          to="/documents"
          class="rounded-md px-3 py-1.5 text-slate-700 hover:bg-slate-100"
          data-testid="nav-documents"
        >
          Browse documents
        </RouterLink>
        <RouterLink
          to="/watchlists"
          class="rounded-md px-3 py-1.5 text-slate-700 hover:bg-slate-100"
          data-testid="nav-watchlists"
        >
          Manage watchlists
        </RouterLink>
        <span v-if="auth.principal" data-testid="user-email" class="text-slate-600">
          {{ auth.principal.email }}
        </span>
        <Button variant="outline" size="sm" data-testid="sign-out" @click="onSignOut">
          Sign out
        </Button>
      </div>
    </div>
  </header>
</template>
