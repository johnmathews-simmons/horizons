<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  computeDiff,
  computeDiffAsync,
  DIFF_WORKER_THRESHOLD,
  type DiffOp,
  type PendingDiff,
} from '@/lib/diff'

interface Props {
  before: string | null
  after: string | null
  mode?: 'side-by-side' | 'unified'
}

const props = withDefaults(defineProps<Props>(), { mode: 'side-by-side' })

const usesWorker = computed(
  () => (props.before?.length ?? 0) + (props.after?.length ?? 0) > DIFF_WORKER_THRESHOLD,
)

const asyncOps = ref<DiffOp[] | null>(null)
const isComputing = ref(false)
let pending: PendingDiff | null = null

function refreshAsync() {
  pending?.cancel()
  pending = null
  if (!usesWorker.value) {
    asyncOps.value = null
    isComputing.value = false
    return
  }
  isComputing.value = true
  asyncOps.value = null
  const handle = computeDiffAsync(props.before, props.after)
  pending = handle
  handle.promise
    .then((ops) => {
      if (pending !== handle) return
      asyncOps.value = ops
      isComputing.value = false
      pending = null
    })
    .catch(() => {
      if (pending !== handle) return
      asyncOps.value = []
      isComputing.value = false
      pending = null
    })
}

watch([() => props.before, () => props.after], () => refreshAsync(), { immediate: true })

onBeforeUnmount(() => {
  pending?.cancel()
  pending = null
})

const ops = computed<DiffOp[]>(() => {
  if (usesWorker.value) return asyncOps.value ?? []
  return computeDiff(props.before, props.after)
})

const beforeOps = computed<DiffOp[]>(() => ops.value.filter((o) => o.op !== 1))
const afterOps = computed<DiffOp[]>(() => ops.value.filter((o) => o.op !== -1))
</script>

<template>
  <div
    v-if="isComputing"
    data-testid="diff-computing"
    class="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-500"
  >
    Computing diff…
  </div>

  <div
    v-else-if="mode === 'unified'"
    class="rounded-md border border-slate-200 bg-white p-4"
  >
    <pre
      data-testid="diff-unified"
      class="whitespace-pre-wrap break-words font-mono text-sm leading-6 text-slate-800"
    ><template v-for="(op, idx) in ops" :key="idx"
      ><ins v-if="op.op === 1" class="bg-green-100 text-green-900 no-underline">{{ op.text }}</ins
      ><del v-else-if="op.op === -1" class="bg-red-100 text-red-900">{{ op.text }}</del
      ><span v-else>{{ op.text }}</span
    ></template></pre>
  </div>

  <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2">
    <div class="rounded-md border border-slate-200 bg-white p-4">
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Before</div>
      <pre
        data-testid="diff-before"
        class="whitespace-pre-wrap break-words font-mono text-sm leading-6 text-slate-800"
      ><template v-for="(op, idx) in beforeOps" :key="idx"
        ><del v-if="op.op === -1" class="bg-red-100 text-red-900">{{ op.text }}</del
        ><span v-else>{{ op.text }}</span
      ></template></pre>
    </div>
    <div class="rounded-md border border-slate-200 bg-white p-4">
      <div class="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">After</div>
      <pre
        data-testid="diff-after"
        class="whitespace-pre-wrap break-words font-mono text-sm leading-6 text-slate-800"
      ><template v-for="(op, idx) in afterOps" :key="idx"
        ><ins v-if="op.op === 1" class="bg-green-100 text-green-900 no-underline">{{ op.text }}</ins
        ><span v-else>{{ op.text }}</span
      ></template></pre>
    </div>
  </div>
</template>
