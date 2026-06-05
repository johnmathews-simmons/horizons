import { describe, expect, it } from 'vitest'
import { sanitiseRedirect } from '../redirect'

describe('sanitiseRedirect', () => {
  it.each([
    ['/', '/'],
    ['/watchlists', '/watchlists'],
    ['/foo?bar=1', '/foo?bar=1'],
    ['/foo#anchor', '/foo#anchor'],
    ['/a/b/c', '/a/b/c'],
  ])('passes through same-origin path %p', (input, expected) => {
    expect(sanitiseRedirect(input)).toBe(expected)
  })

  it.each([
    // Protocol-relative — the original vulnerability.
    ['//evil.com', '/'],
    ['//evil.com/path', '/'],
    // Backslash-prefixed — IE / some browsers normalise this to '/'.
    ['/\\evil.com', '/'],
    ['/\\/evil.com', '/'],
    // Absolute URL.
    ['https://evil.com', '/'],
    ['http://evil.com/path', '/'],
    // Schemes.
    ['javascript:alert(1)', '/'],
    ['data:text/html,<script>alert(1)</script>', '/'],
    // No leading slash.
    ['evil.com', '/'],
    ['?redirect=foo', '/'],
    // Empty.
    ['', '/'],
  ])('rejects %p and falls back to /', (input, expected) => {
    expect(sanitiseRedirect(input)).toBe(expected)
  })

  it.each([
    [undefined],
    [null],
    [42],
    [['/foo']],
    [{ path: '/foo' }],
  ])('falls back to / for non-string %p', (input) => {
    expect(sanitiseRedirect(input)).toBe('/')
  })
})
