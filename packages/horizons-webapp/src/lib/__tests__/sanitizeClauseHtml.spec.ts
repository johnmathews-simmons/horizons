import { describe, it, expect } from 'vitest'
import { looksLikeHtml, sanitizeClauseHtml } from '../sanitizeClauseHtml'

describe('looksLikeHtml', () => {
  it('returns true for content with a real tag', () => {
    expect(looksLikeHtml('<p>hi</p>')).toBe(true)
    expect(looksLikeHtml('plain prose with <em>emphasis</em> inside')).toBe(true)
  })

  it('returns false for plain text containing stray <', () => {
    expect(looksLikeHtml('if x < y then')).toBe(false)
    expect(looksLikeHtml('completely plain prose')).toBe(false)
  })
})

describe('sanitizeClauseHtml', () => {
  it('drops <script> tags', () => {
    const out = sanitizeClauseHtml('<p>ok</p><script>alert(1)</script>')
    expect(out).not.toMatch(/<script/i)
    expect(out).toContain('<p>ok</p>')
  })

  it('drops event-handler attributes and javascript: URLs', () => {
    const out = sanitizeClauseHtml(
      '<a href="javascript:alert(1)" onclick="alert(1)">click</a>',
    )
    expect(out).not.toMatch(/javascript:/i)
    expect(out).not.toMatch(/onclick/i)
  })

  it('adds target=_blank and rel=noopener noreferrer to anchors', () => {
    const out = sanitizeClauseHtml('<a href="https://example.com">x</a>')
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it('preserves table structure', () => {
    const out = sanitizeClauseHtml(
      '<table><thead><tr><th>h</th></tr></thead><tbody><tr><td colspan="2">v</td></tr></tbody></table>',
    )
    expect(out).toContain('<table>')
    expect(out).toContain('<th>h</th>')
    expect(out).toContain('colspan="2"')
  })

  it('preserves breadcrumb-shaped ordered lists', () => {
    const out = sanitizeClauseHtml(
      '<ol><li><a href="https://h.example/">Home</a></li><li>Doc</li></ol>',
    )
    expect(out).toContain('<ol>')
    expect(out).toContain('<li>')
    expect(out).toContain('Home')
  })

  it('drops iframes and style/link tags', () => {
    const out = sanitizeClauseHtml(
      '<iframe src="x"></iframe><style>body{}</style><link rel="stylesheet" href="x">',
    )
    expect(out).not.toMatch(/<iframe/i)
    expect(out).not.toMatch(/<style/i)
    expect(out).not.toMatch(/<link/i)
  })
})
