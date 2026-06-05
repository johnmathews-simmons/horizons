<script setup lang="ts">
import { useRouter } from 'vue-router'
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
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <Button variant="outline" size="sm" data-testid="sign-out" @click="onSignOut">
          Sign out
        </Button>
      </div>
    </header>

    <section class="mx-auto max-w-3xl px-6 py-16">
      <h1 class="text-3xl font-semibold tracking-tight text-slate-900">Welcome</h1>
      <p class="mt-3 text-slate-600">
        Regulatory-change intelligence. Application views land in later work units.
      </p>
    </section>
  </main>
</template>
