import { computeDiff, type DiffOp } from '@/lib/diff'

export interface DiffRequest {
  before: string
  after: string
}

export interface DiffResponse {
  ops: DiffOp[]
}

// Exported separately from the worker boundary so the handler can be
// unit-tested without spinning up a real Worker (jsdom doesn't ship one).
export function handleDiffMessage(request: DiffRequest): DiffOp[] {
  return computeDiff(request.before, request.after)
}

// `self` is the dedicated-worker global scope when bundled via Vite's
// `?worker` import. Guard so importing this module in a non-worker context
// (the unit test) doesn't clobber a window's onmessage handler.
interface WorkerScope {
  onmessage: ((event: MessageEvent<DiffRequest>) => void) | null
  postMessage(message: DiffResponse): void
}
const ctx = self as unknown as WorkerScope
if (typeof window === 'undefined' && typeof ctx.postMessage === 'function') {
  ctx.onmessage = (event: MessageEvent<DiffRequest>) => {
    const ops = handleDiffMessage(event.data)
    ctx.postMessage({ ops })
  }
}
