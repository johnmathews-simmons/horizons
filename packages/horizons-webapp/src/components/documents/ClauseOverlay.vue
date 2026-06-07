<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ClauseItem } from '@/api/documents'
import { looksLikeHtml, sanitizeClauseHtml } from '@/lib/sanitizeClauseHtml'

interface Props {
  clauses: ClauseItem[]
  showStructure: boolean
  highlightPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { highlightPath: null })

interface DepthClause {
  clause: ClauseItem
  depth: number
  headingTag: string
}

// Depth 0 → h2 … depth ≥4 → h6. h1 is reserved for the document title
// in the page header above the panes.
function tagForDepth(depth: number): string {
  const level = Math.min(6, Math.max(2, depth + 2))
  return `h${level}`
}

const decorated = computed<DepthClause[]>(() =>
  props.clauses.map((c) => {
    const depth = Math.max(0, c.clause_path.split('/').length - 1)
    return {
      clause: c,
      depth,
      headingTag: tagForDepth(depth),
    }
  }),
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
  // flat and structure modes — both emit data-clause-path on the
  // clause's outer container.
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

function hasHeading(c: ClauseItem): boolean {
  return c.heading_text !== null && c.heading_text !== undefined && c.heading_text.trim().length > 0
}

function hasBody(c: ClauseItem): boolean {
  return c.text_content.trim().length > 0
}

function isHtmlBody(c: ClauseItem): boolean {
  return looksLikeHtml(c.text_content)
}

function htmlBody(c: ClauseItem): string {
  return sanitizeClauseHtml(c.text_content)
}

const HEADING_BASE = 'mb-2 mt-4 font-semibold text-slate-900'
const HEADING_BY_TAG: Record<string, string> = {
  h2: 'text-xl border-b border-slate-200 pb-1',
  h3: 'text-lg',
  h4: 'text-base',
  h5: 'text-sm uppercase tracking-wide text-slate-700',
  h6: 'text-xs uppercase tracking-wide text-slate-600',
}

function headingClass(tag: string): string {
  return `${HEADING_BASE} ${HEADING_BY_TAG[tag] ?? ''}`.trim()
}
</script>

<template>
  <div ref="root" data-testid="document-body" class="bg-white p-6 shadow-sm">
    <!-- Continuous mode: clauses run together, parser boundaries invisible. -->
    <template v-if="!showStructure">
      <article class="prose prose-slate max-w-none">
        <div
          v-for="dc in decorated"
          :key="dc.clause.id"
          data-testid="clause-flat"
          :data-clause-path="dc.clause.clause_path"
          :data-highlight="isHighlighted(dc.clause.clause_path) ? 'true' : undefined"
          class="mb-4"
          :class="
            isHighlighted(dc.clause.clause_path)
              ? 'rounded-md bg-amber-100 ring-2 ring-amber-400 p-3'
              : ''
          "
        >
          <component
            :is="dc.headingTag"
            v-if="hasHeading(dc.clause)"
            data-testid="clause-heading"
            :data-heading-level="dc.headingTag"
            :class="headingClass(dc.headingTag)"
          >
            {{ dc.clause.heading_text }}
          </component>
          <template v-if="hasBody(dc.clause)">
            <div
              v-if="isHtmlBody(dc.clause)"
              data-testid="clause-body-html"
              class="clause-html font-serif text-base text-slate-800"
              v-html="htmlBody(dc.clause)"
            />
            <pre
              v-else
              data-testid="clause-body-text"
              class="whitespace-pre-wrap font-serif text-base text-slate-800"
            >{{ dc.clause.text_content }}</pre>
          </template>
        </div>
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
          <div class="px-3 py-3">
            <component
              :is="dc.headingTag"
              v-if="hasHeading(dc.clause)"
              data-testid="clause-heading"
              :data-heading-level="dc.headingTag"
              :class="headingClass(dc.headingTag)"
            >
              {{ dc.clause.heading_text }}
            </component>
            <template v-if="hasBody(dc.clause)">
              <div
                v-if="isHtmlBody(dc.clause)"
                data-testid="clause-body-html"
                class="clause-html font-serif text-sm text-slate-800"
                v-html="htmlBody(dc.clause)"
              />
              <pre
                v-else
                data-testid="clause-body-text"
                class="whitespace-pre-wrap font-serif text-sm text-slate-800"
              >{{ dc.clause.text_content }}</pre>
            </template>
          </div>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
/* Light-touch styling for sanitized upstream HTML — tables, lists,
   anchors, images. Keeps the rendered output readable without
   overpowering the prose blocks that surround it. */
.clause-html :deep(ol),
.clause-html :deep(ul) {
  margin: 0.25rem 0 0.5rem 1.25rem;
  list-style-position: outside;
}
.clause-html :deep(ol) {
  list-style-type: decimal;
}
.clause-html :deep(ul) {
  list-style-type: disc;
}
.clause-html :deep(a) {
  color: rgb(30 64 175); /* blue-800 */
  text-decoration: underline;
}
.clause-html :deep(table) {
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.85em;
  width: 100%;
}
.clause-html :deep(td),
.clause-html :deep(th) {
  border: 1px solid rgb(203 213 225); /* slate-300 */
  padding: 0.25rem 0.5rem;
  vertical-align: top;
}
.clause-html :deep(th) {
  background-color: rgb(241 245 249); /* slate-100 */
  font-weight: 600;
}
.clause-html :deep(img) {
  max-width: 100%;
  height: auto;
  margin: 0.5rem 0;
}
.clause-html :deep(blockquote) {
  border-left: 3px solid rgb(203 213 225);
  padding-left: 0.75rem;
  margin: 0.5rem 0;
  color: rgb(71 85 105); /* slate-600 */
}
</style>
