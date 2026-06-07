/**
 * Sanitize the small surface of HTML our legal corpus contains.
 *
 * Upstream markdown sometimes carries raw ``html_block`` content
 * (scraped breadcrumbs, ``<table>``-shaped questionnaires in the BE/AL
 * fixtures, embedded images). The parser preserves these blocks
 * verbatim inside ``clauses.text_content``; this helper renders them as
 * actual HTML in the browser instead of as escaped source.
 *
 * Allowlist is deliberately tight — structural and link/table tags
 * only, no ``script``/``style``/``iframe``, no event-handler
 * attributes, no ``javascript:`` URLs. External anchors are forced to
 * open in a new tab with ``rel="noopener noreferrer"`` so a hostile
 * link cannot reach back into our app via ``window.opener``.
 */

import DOMPurify from 'dompurify'

const ALLOWED_TAGS = [
  'a',
  'b',
  'strong',
  'em',
  'i',
  'u',
  'span',
  'br',
  'p',
  'ol',
  'ul',
  'li',
  'blockquote',
  'code',
  'pre',
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'td',
  'th',
  'caption',
  'figure',
  'figcaption',
  'img',
  'sub',
  'sup',
  'hr',
]

const ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'colspan', 'rowspan']

let hookInstalled = false

function installSafeLinkHook(): void {
  if (hookInstalled) return
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (!(node instanceof Element)) return
    if (node.tagName === 'A' && node.hasAttribute('href')) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  })
  hookInstalled = true
}

/**
 * Heuristic: does ``text`` contain at least one HTML-element-shaped
 * token? Used to decide whether to route a clause body through the
 * sanitizer or render it as plain text.
 *
 * The regex matches an opening tag-name pattern; stray ``<`` characters
 * in plain prose (``x < y``) do not match because the next character
 * must be a letter.
 */
export function looksLikeHtml(text: string): boolean {
  return /<[a-zA-Z][^>]*>/.test(text)
}

/** Return a sanitized HTML string safe for ``v-html``. */
export function sanitizeClauseHtml(text: string): string {
  installSafeLinkHook()
  return DOMPurify.sanitize(text, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
  })
}
