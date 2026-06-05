<script setup lang="ts">
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { ToastViewport } from '@/components/ui/toast'

const auth = useAuthStore()
const router = useRouter()

async function onSignOut(): Promise<void> {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <div class="flex items-center gap-6">
          <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons admin</span>
          <nav class="flex gap-4 text-sm" aria-label="Admin sections">
            <RouterLink
              :to="{ name: 'admin-clients' }"
              class="text-slate-600 hover:text-slate-900"
              active-class="text-slate-900 font-medium"
              data-testid="nav-admin-clients"
            >
              Clients
            </RouterLink>
            <RouterLink
              :to="{ name: 'admin-audit' }"
              class="text-slate-600 hover:text-slate-900"
              active-class="text-slate-900 font-medium"
              data-testid="nav-admin-audit"
            >
              Audit log
            </RouterLink>
          </nav>
        </div>
        <div class="flex items-center gap-3 text-sm text-slate-600">
          <span v-if="auth.principal" data-testid="admin-email">{{ auth.principal.email }}</span>
          <Button variant="outline" size="sm" data-testid="sign-out" @click="onSignOut">
            Sign out
          </Button>
        </div>
      </div>
    </header>
    <RouterView />
    <ToastViewport />
  </main>
</template>
