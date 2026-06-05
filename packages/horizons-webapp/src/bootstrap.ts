import { createApp, type App } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'

import RootApp from '@/App.vue'
import router from '@/router'
import { configureApiClient, setAuthBridge } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToast } from '@/composables/useToast'
import { fetchAndValidateConfig, setRuntimeConfig } from '@/config/runtime'

export interface BootstrapResult {
  status: 'mounted' | 'failed'
  reason?: string
  app?: App
}

/**
 * Fetch + validate /config.json, configure the API client, then mount the Vue
 * app. On any failure, render a fail-loud error screen and DO NOT mount —
 * silently falling back to dev defaults in a deployed environment hides
 * config drift until a customer reports it.
 */
export async function bootstrap(rootSelector: string = '#app'): Promise<BootstrapResult> {
  const root = document.querySelector(rootSelector)
  if (!root) {
    return { status: 'failed', reason: `root element ${rootSelector} not found` }
  }

  let configReason: string | null = null
  try {
    const config = await fetchAndValidateConfig()
    setRuntimeConfig(config)
    configureApiClient(config.apiBaseUrl)
  } catch (err) {
    configReason = err instanceof Error ? err.message : String(err)
  }

  if (configReason !== null) {
    renderConfigErrorScreen(root, configReason)
    return { status: 'failed', reason: configReason }
  }

  const app = createApp(RootApp)
  app.use(createPinia())
  app.use(router)
  app.use(VueQueryPlugin)

  const auth = useAuthStore()
  const toast = useToast()
  setAuthBridge({
    getAccessToken: () => auth.accessToken,
    getKind: () => auth.kind,
    refresh: () => auth.refresh(),
    onAuthFailure: () => {
      auth.clear()
      void router.push({ name: 'login' })
    },
    onImpersonationExpired: () => {
      // [[adversary class 6]] — drop impersonation state, restore admin
      // context from the captured in-memory snapshot, surface the event.
      auth.exitSupportView()
      toast.error('Support view expired', 'The impersonation token has expired. You are back in your admin session.')
      void router.push({ name: 'admin-clients' })
    },
  })

  app.mount(root)
  return { status: 'mounted', app }
}

function renderConfigErrorScreen(root: Element, reason: string): void {
  while (root.firstChild) {
    root.removeChild(root.firstChild)
  }

  const container = document.createElement('div')
  container.setAttribute('data-testid', 'config-error')
  container.setAttribute(
    'style',
    'font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 2rem; color: #1f2937;',
  )

  const heading = document.createElement('h1')
  heading.textContent = 'Configuration error'
  heading.setAttribute('style', 'color: #b91c1c; font-size: 1.5rem; margin: 0 0 1rem;')
  container.appendChild(heading)

  const lead = document.createElement('p')
  lead.textContent = 'Horizons could not load its runtime configuration. The application has not started.'
  lead.setAttribute('style', 'margin: 0 0 1rem;')
  container.appendChild(lead)

  const reasonLabel = document.createElement('p')
  reasonLabel.setAttribute('style', 'margin: 0 0 0.5rem; font-weight: 600;')
  reasonLabel.textContent = 'Reason'
  container.appendChild(reasonLabel)

  const reasonPre = document.createElement('pre')
  reasonPre.setAttribute(
    'style',
    'background: #fef2f2; border: 1px solid #fecaca; padding: 1rem; border-radius: 0.375rem; white-space: pre-wrap; word-wrap: break-word; font-family: ui-monospace, monospace; font-size: 0.875rem; color: #7f1d1d; margin: 0 0 1.5rem;',
  )
  reasonPre.textContent = reason
  reasonPre.setAttribute('data-testid', 'config-error-reason')
  container.appendChild(reasonPre)

  const help = document.createElement('p')
  help.setAttribute('style', 'color: #4b5563; font-size: 0.875rem; margin: 0;')
  help.textContent =
    'This usually means /config.json is missing from the deployment or its shape does not match the expected schema. Contact an administrator.'
  container.appendChild(help)

  root.appendChild(container)
}
