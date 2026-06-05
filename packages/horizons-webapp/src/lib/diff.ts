import DiffMatchPatch from 'diff-match-patch'
import DiffWorker from '@/workers/diff.worker?worker'

// The diff-match-patch type emits a raw `[op, text]` tuple per change.
// Wrap it in a friendlier object so downstream renderers don't index
// into anonymous tuples.

export type DiffOpKind = -1 | 0 | 1

export interface DiffOp {
  op: DiffOpKind
  text: string
}

export function computeDiff(before: string | null, after: string | null): DiffOp[] {
  const dmp = new DiffMatchPatch()
  const raw = dmp.diff_main(before ?? '', after ?? '')
  dmp.diff_cleanupSemantic(raw)
  return raw.map(([op, text]) => ({ op: op as DiffOpKind, text }))
}

// Combined-length threshold above which the diff is offloaded to a Web Worker.
// 50K leaves the existing sync path covering everyday clause edits while
// catching the long-tail clauses (the AL fixture has blank-line blocks up to
// ~88KB) that would otherwise jank the main thread.
export const DIFF_WORKER_THRESHOLD = 50_000

export interface PendingDiff {
  promise: Promise<DiffOp[]>
  cancel(): void
}

interface ComputeDiffAsyncOptions {
  threshold?: number
  workerFactory?: () => Worker
}

interface WorkerReply {
  ops: DiffOp[]
}

function defaultWorkerFactory(): Worker {
  return new DiffWorker()
}

export function computeDiffAsync(
  before: string | null,
  after: string | null,
  opts: ComputeDiffAsyncOptions = {},
): PendingDiff {
  const threshold = opts.threshold ?? DIFF_WORKER_THRESHOLD
  const combined = (before?.length ?? 0) + (after?.length ?? 0)

  if (combined <= threshold) {
    return {
      promise: Promise.resolve(computeDiff(before, after)),
      cancel: () => {},
    }
  }

  const factory = opts.workerFactory ?? defaultWorkerFactory
  const worker = factory()
  let settled = false

  const promise = new Promise<DiffOp[]>((resolve, reject) => {
    worker.onmessage = (event: MessageEvent<WorkerReply>) => {
      settled = true
      worker.terminate()
      resolve(event.data.ops)
    }
    worker.onerror = (event: ErrorEvent) => {
      settled = true
      worker.terminate()
      reject(event)
    }
    worker.postMessage({ before: before ?? '', after: after ?? '' })
  })

  return {
    promise,
    cancel: () => {
      if (settled) return
      settled = true
      worker.terminate()
    },
  }
}
