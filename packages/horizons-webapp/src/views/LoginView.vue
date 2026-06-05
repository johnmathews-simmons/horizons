<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import { sanitiseRedirect } from '@/router/redirect'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const submitting = ref(false)
const errorMessage = ref<string | null>(null)

async function onSubmit(): Promise<void> {
  submitting.value = true
  errorMessage.value = null
  try {
    await auth.login({ email: email.value, password: password.value })
    await router.push(sanitiseRedirect(route.query.redirect))
  } catch (err: unknown) {
    if (axios.isAxiosError(err) && err.response?.status === 401) {
      errorMessage.value = 'Invalid email or password.'
    } else {
      errorMessage.value = 'Sign-in failed. Please try again.'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-slate-50 px-4">
    <section class="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
      <header class="mb-6">
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Sign in to Horizons</h1>
        <p class="mt-1 text-sm text-slate-500">Regulatory-change intelligence.</p>
      </header>

      <form class="space-y-4" @submit.prevent="onSubmit">
        <div class="space-y-2">
          <Label for="email">Email</Label>
          <Input
            id="email"
            v-model="email"
            type="email"
            autocomplete="username"
            required
            data-testid="email-input"
          />
        </div>

        <div class="space-y-2">
          <Label for="password">Password</Label>
          <Input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            data-testid="password-input"
          />
        </div>

        <p
          v-if="errorMessage"
          role="alert"
          data-testid="login-error"
          class="text-sm text-red-600"
        >
          {{ errorMessage }}
        </p>

        <Button type="submit" :disabled="submitting" class="w-full" data-testid="login-submit">
          {{ submitting ? 'Signing in…' : 'Sign in' }}
        </Button>
      </form>
    </section>
  </main>
</template>
