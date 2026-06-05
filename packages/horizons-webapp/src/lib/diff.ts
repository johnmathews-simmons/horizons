import DiffMatchPatch from 'diff-match-patch'

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
