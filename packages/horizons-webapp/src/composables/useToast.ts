import { reactive, readonly } from 'vue'

export type ToastVariant = 'success' | 'error'

export interface Toast {
  id: number
  title: string
  description?: string
  variant: ToastVariant
}

interface ToastState {
  toasts: Toast[]
}

const state = reactive<ToastState>({ toasts: [] })
let nextId = 1
const DEFAULT_TIMEOUT_MS = 4_000

function dismiss(id: number): void {
  const index = state.toasts.findIndex((t) => t.id === id)
  if (index !== -1) {
    state.toasts.splice(index, 1)
  }
}

function push(toast: Omit<Toast, 'id'>): number {
  const id = nextId
  nextId += 1
  state.toasts.push({ id, ...toast })
  // jsdom honours setTimeout; tests can flush via vi.useFakeTimers() if needed.
  setTimeout(() => dismiss(id), DEFAULT_TIMEOUT_MS)
  return id
}

export function useToast() {
  return {
    toasts: readonly(state.toasts),
    success: (title: string, description?: string) => push({ title, description, variant: 'success' }),
    error: (title: string, description?: string) => push({ title, description, variant: 'error' }),
    dismiss,
  }
}

/** Test helper — wipes the active toast queue between specs. */
export function _resetToasts(): void {
  state.toasts.splice(0, state.toasts.length)
}
