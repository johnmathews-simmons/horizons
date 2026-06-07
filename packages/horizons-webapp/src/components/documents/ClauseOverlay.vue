<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ClauseItem } from '@/api/documents'
import { looksLikeHtml, sanitizeClauseHtml } from '@/lib/sanitizeClauseHtml'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

interface Props {
  clauses: ClauseItem[]
  showStructure: boolean
  changeMap?: Record<string, ChangeType> | null
  scrollToPath?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  changeMap: null,
  scrollToPath: null,
})

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

function scrollToTarget(): void {
  const target = props.scrollToPath
  if (!target) return
  if (!root.value) return
  const match = props.clauses.find((c) => c.clause_path === target)
  if (!match) {
    console.warn(`ClauseOverlay: scrollToPath "${target}" not found in clauses`)
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
  void nextTick().then(() => scrollToTarget())
})

watch(
  () => [props.scrollToPath, props.clauses.length] as const,
  () => {
    void nextTick().then(() => scrollToTarget())
  },
)

function changeTypeFor(path: string): ChangeType | null {
  if (!props.changeMap) return null
  return props.changeMap[path] ?? null
}

function hasHeading(c: ClauseItem): boolean {
  return c.heading_text !== null && c.heading_text !== undefined && c.heading_text.trim().length > 0
}

function hasBody(c: ClauseItem): boolean {
  return c.text_content.trim().length > 0
}

function hasNumbering(c: ClauseItem): boolean {
  return (
    c.numbering_label !== null &&
    c.numbering_label !== undefined &&
    c.numbering_label.trim().length > 0
  )
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
          :data-change-type="changeTypeFor(dc.clause.clause_path) ?? undefined"
          class="relative mb-4"
          :class="
            changeTypeFor(dc.clause.clause_path)
              ? CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].box
              : ''
          "
        >
          <span
            v-if="changeTypeFor(dc.clause.clause_path)"
            data-testid="clause-change-pill"
            class="absolute right-2 top-2 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset"
            :class="CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].pill"
          >
            {{ CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].label }}
          </span>
          <component
            :is="dc.headingTag"
            v-if="hasHeading(dc.clause)"
            data-testid="clause-heading"
            :data-heading-level="dc.headingTag"
            :class="headingClass(dc.headingTag)"
          >
            {{ dc.clause.heading_text }}
          </component>
          <span
            v-if="hasNumbering(dc.clause)"
            data-testid="clause-numbering"
            class="mr-2 inline-block font-bold text-slate-900"
          >{{ dc.clause.numbering_label }}</span>
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
          :data-change-type="changeTypeFor(dc.clause.clause_path) ?? undefined"
          class="relative rounded-md border border-slate-200"
          :class="
            changeTypeFor(dc.clause.clause_path)
              ? CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].box
              : 'bg-slate-50'
          "
          :style="{ marginLeft: `${dc.depth * 1.25}rem` }"
        >
          <span
            v-if="changeTypeFor(dc.clause.clause_path)"
            data-testid="clause-change-pill"
            class="absolute right-2 top-2 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset"
            :class="CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].pill"
          >
            {{ CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].label }}
          </span>
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
