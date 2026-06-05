import { describe, expect, it, vi } from 'vitest'
import {
  computeDiff,
  computeDiffAsync,
  DIFF_WORKER_THRESHOLD,
  type DiffOp,
} from '@/lib/diff'

describe('computeDiff', () => {
  it('returns a single equal op for identical strings', () => {
    const ops = computeDiff('hello world', 'hello world')
    expect(ops).toEqual([{ op: 0, text: 'hello world' }])
  })

  it('returns a single insert when before is empty', () => {
    const ops = computeDiff('', 'new clause text')
    expect(ops).toEqual([{ op: 1, text: 'new clause text' }])
  })

  it('returns a single delete when after is empty', () => {
    const ops = computeDiff('old clause text', '')
    expect(ops).toEqual([{ op: -1, text: 'old clause text' }])
  })

  it('returns mixed ops for a real edit', () => {
    const ops = computeDiff('within 6 months', 'within 12 months')
    // The diff should contain at least one delete of "6" and one insert of "12"
    expect(ops.some((o) => o.op === -1 && o.text.includes('6'))).toBe(true)
    expect(ops.some((o) => o.op === 1 && o.text.includes('12'))).toBe(true)
    // The reconstruction of the before/after sides must match the originals
    const before = ops
      .filter((o) => o.op !== 1)
      .map((o) => o.text)
      .join('')
    const after = ops
      .filter((o) => o.op !== -1)
      .map((o) => o.text)
      .join('')
    expect(before).toBe('within 6 months')
    expect(after).toBe('within 12 months')
  })

  it('coerces null before to empty string (treats as ADDED)', () => {
    const ops = computeDiff(null, 'brand new')
    expect(ops).toEqual([{ op: 1, text: 'brand new' }])
  })

  it('coerces null after to empty string (treats as REMOVED)', () => {
    const ops = computeDiff('was here', null)
    expect(ops).toEqual([{ op: -1, text: 'was here' }])
  })
})

// jsdom doesn't ship a Worker; this fake matches the surface computeDiffAsync uses.
class FakeWorker {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: ErrorEvent) => void) | null = null
  postMessage = vi.fn<(payload: unknown) => void>()
  terminate = vi.fn<() => void>()
}

describe('computeDiffAsync', () => {
  it('routes under-threshold inputs through the sync path without creating a worker', async () => {
    const workerFactory = vi.fn<() => Worker>(() => new FakeWorker() as unknown as Worker)
    const handle = computeDiffAsync('within 6 months', 'within 12 months', {
      workerFactory,
      threshold: DIFF_WORKER_THRESHOLD,
    })

    expect(workerFactory).not.toHaveBeenCalled()
    await expect(handle.promise).resolves.toEqual(
      computeDiff('within 6 months', 'within 12 months'),
    )
  })

  it('treats input equal to the threshold as under-threshold (sync)', async () => {
    const workerFactory = vi.fn<() => Worker>(() => new FakeWorker() as unknown as Worker)
    const before = 'a'.repeat(5)
    const after = 'a'.repeat(5)
    const handle = computeDiffAsync(before, after, {
      workerFactory,
      threshold: 10, // combined length is exactly threshold
    })

    expect(workerFactory).not.toHaveBeenCalled()
    await expect(handle.promise).resolves.toEqual(computeDiff(before, after))
  })

  it('routes over-threshold inputs through the worker', async () => {
    const fake = new FakeWorker()
    const workerFactory = vi.fn<() => Worker>(() => fake as unknown as Worker)
    const handle = computeDiffAsync('xxxxxx', 'yyyyyy', {
      workerFactory,
      threshold: 1,
    })

    expect(workerFactory).toHaveBeenCalledTimes(1)
    expect(fake.postMessage).toHaveBeenCalledWith({ before: 'xxxxxx', after: 'yyyyyy' })

    const reply: DiffOp[] = [
      { op: -1, text: 'xxxxxx' },
      { op: 1, text: 'yyyyyy' },
    ]
    fake.onmessage?.({ data: { ops: reply } } as MessageEvent)

    await expect(handle.promise).resolves.toEqual(reply)
    expect(fake.terminate).toHaveBeenCalledTimes(1)
  })

  it('coerces null sides to empty strings before posting to the worker', () => {
    const fake = new FakeWorker()
    const workerFactory = vi.fn<() => Worker>(() => fake as unknown as Worker)
    computeDiffAsync(null, 'z'.repeat(200), { workerFactory, threshold: 1 })

    expect(fake.postMessage).toHaveBeenCalledWith({ before: '', after: 'z'.repeat(200) })
  })

  it('cancel() terminates the worker before it has replied', () => {
    const fake = new FakeWorker()
    const workerFactory = () => fake as unknown as Worker
    const handle = computeDiffAsync('xxxxxx', 'yyyyyy', {
      workerFactory,
      threshold: 1,
    })

    handle.cancel()
    expect(fake.terminate).toHaveBeenCalledTimes(1)
  })

  it('cancel() after the worker has already replied does not double-terminate', async () => {
    const fake = new FakeWorker()
    const workerFactory = () => fake as unknown as Worker
    const handle = computeDiffAsync('xxxxxx', 'yyyyyy', {
      workerFactory,
      threshold: 1,
    })

    fake.onmessage?.({ data: { ops: [] } } as MessageEvent)
    await handle.promise

    handle.cancel()
    expect(fake.terminate).toHaveBeenCalledTimes(1)
  })

  it('rejects when the worker raises an error and terminates it', async () => {
    const fake = new FakeWorker()
    const workerFactory = () => fake as unknown as Worker
    const handle = computeDiffAsync('xxxxxx', 'yyyyyy', {
      workerFactory,
      threshold: 1,
    })

    fake.onerror?.({ message: 'boom' } as ErrorEvent)
    await expect(handle.promise).rejects.toBeDefined()
    expect(fake.terminate).toHaveBeenCalledTimes(1)
  })

  it('DIFF_WORKER_THRESHOLD is calibrated for clause-level diffs (not whole-document)', () => {
    // 50K combined leaves room for realistic clause-vs-clause comparisons
    // (the AL fixture's largest blank-line block is ~88KB) without paying the
    // worker tax on small everyday edits.
    expect(DIFF_WORKER_THRESHOLD).toBe(50_000)
  })
})
