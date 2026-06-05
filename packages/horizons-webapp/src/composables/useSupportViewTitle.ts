import { watchEffect } from 'vue'
import { useAuthStore } from '@/stores/auth'

const SUPPORT_PREFIX = '[SUPPORT] '
const BASE_TITLE = 'Horizons'

/**
 * Side-effect composable: keeps `document.title` prefixed with `[SUPPORT]`
 * for the lifetime of the impersonation state. Restores the bare title on
 * exit. Mounted once at the App.vue root so the title invariant survives
 * every route change. See [[adversary class 3]] in the WU5.4 journal —
 * the tab title is a defence-in-depth signal for the case where the
 * amber banner doesn't render (RTL, narrow viewport, screen reader, CSS
 * override).
 */
export function useSupportViewTitle(): void {
  if (typeof document === 'undefined') return
  const auth = useAuthStore()
  watchEffect(() => {
    if (auth.impersonationState) {
      if (!document.title.startsWith(SUPPORT_PREFIX)) {
        document.title = SUPPORT_PREFIX + (document.title || BASE_TITLE)
      }
    } else if (document.title.startsWith(SUPPORT_PREFIX)) {
      document.title = document.title.slice(SUPPORT_PREFIX.length) || BASE_TITLE
    }
  })
}
