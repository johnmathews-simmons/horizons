<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ClauseItem } from '@/api/documents'

interface Props {
  clauses: ClauseItem[]
  showStructure: boolean
  highlightPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { highlightPath: null })

interface DepthClause {
  clause: ClauseItem
  depth: number
}

const decorated = computed<DepthClause[]>(() =>
  props.clauses.map((c) => ({
    clause: c,
    depth: Math.max(0, c.clause_path.split('/').length - 1),
  })),
)

const root = ref<HTMLElement | null>(null)

function scrollToHighlight(): void {
  const target = props.highlightPath
  if (!target) return
  if (!root.value) return
  const match = props.clauses.find((c) => c.clause_path === target)
  if (!match) {
    console.warn(`ClauseOverlay: highlightPath "${target}" not found in clauses`)
    return
  }
  // Look up the rendered element by its data attribute. Works for both
  // flat and structure modes — both emit data-clause-path.
  // Inside an attribute value selector [attr="…"] only backslash and
  // the quote delimiter need escaping; spaces are fine as-is.
  const safe = target.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  const el = root.value.querySelector(`[data-clause-path="${safe}"]`)
  if (!(el instanceof HTMLElement)) return
  if (typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'center', behavior: 'auto' })
  }
}

onMounted(() => {
  void nextTick().then(() => scrollToHighlight())
})

watch(
  () => [props.highlightPath, props.clauses.length] as const,
  () => {
    void nextTick().then(() => scrollToHighlight())
  },
)

function isHighlighted(path: string): boolean {
  return props.highlightPath !== null && props.highlightPath === path
}
</script>

<template>
  <div ref="root" data-testid="document-body" class="bg-white p-6 shadow-sm">
    <!-- Continuous mode: clauses run together, parser boundaries invisible. -->
    <template v-if="!showStructure">
      <article class="prose prose-slate max-w-none">
        <pre
          v-for="dc in decorated"
          :key="dc.clause.id"
          data-testid="clause-flat"
          :data-clause-path="dc.clause.clause_path"
          :data-highlight="isHighlighted(dc.clause.clause_path) ? 'true' : undefined"
          class="mb-4 whitespace-pre-wrap font-serif text-base text-slate-800"
          :class="
            isHighlighted(dc.clause.clause_path)
              ? 'rounded-md bg-amber-100 ring-2 ring-amber-400 p-3'
              : ''
          "
        >{{ dc.clause.text_content }}</pre>
      </article>
    </template>

    <!-- Structure mode: parser's clause boundaries visible as cards with -->
    <!-- anchor chips. Depth comes from the clause_path segment count. -->
    <template v-else>
      <ol class="space-y-3" data-testid="clause-cards">
        <li
          v-for="dc in decorated"
          :key="dc.clause.id"
          data-testid="clause-card"
          :data-clause-path="dc.clause.clause_path"
          :data-depth="dc.depth"
          :data-highlight="isHighlighted(dc.clause.clause_path) ? 'true' : undefined"
          class="rounded-md border border-slate-200 bg-slate-50"
          :class="
            isHighlighted(dc.clause.clause_path)
              ? 'ring-2 ring-amber-400 bg-amber-50'
              : ''
          "
          :style="{ marginLeft: `${dc.depth * 1.25}rem` }"
        >
          <div
            class="flex items-center justify-between border-b border-slate-200 px-3 py-1.5"
          >
            <code
              data-testid="clause-anchor"
              class="rounded bg-slate-200 px-1.5 py-0.5 text-xs font-medium text-slate-700"
            >
              {{ dc.clause.clause_path }}
            </code>
            <span class="text-xs text-slate-400">#{{ dc.clause.ord }}</span>
          </div>
          <pre
            class="whitespace-pre-wrap px-3 py-3 font-serif text-sm text-slate-800"
          >{{ dc.clause.text_content }}</pre>
        </li>
      </ol>
    </template>
  </div>
</template>
