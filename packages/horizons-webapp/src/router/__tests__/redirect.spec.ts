import { describe, expect, it } from 'vitest'
import { sanitiseRedirect } from '../redirect'

describe('sanitiseRedirect', () => {
  it.each([
    ['/', '/'],
    ['/watchlists', '/watchlists'],
    ['/foo?bar=1', '/foo?bar=1'],
    ['/foo#anchor', '/foo#anchor'],
    ['/a/b/c', '/a/b/c'],
    // Percent-encoded tab is NOT stripped by the URL parser (only literal
    // tab/CR/LF are), so this stays as an opaque-path same-origin string.
    // Logged here so a future change that drops it would be a deliberate
    // tightening, not an accident.
    ['/%09/evil.com', '/%09/evil.com'],
  ])('passes through same-origin path %p', (input, expected) => {
    expect(sanitiseRedirect(input)).toBe(expected)
  })

  it.each([
    // Protocol-relative — the original vulnerability.
    ['//evil.com', '/'],
    ['//evil.com/path', '/'],
    // Backslash-prefixed — browsers normalise this to '/'.
    ['/\\evil.com', '/'],
    ['/\\/evil.com', '/'],
    // Absolute URL.
    ['https://evil.com', '/'],
    ['http://evil.com/path', '/'],
    // Schemes.
    ['javascript:alert(1)', '/'],
    ['data:text/html,<script>alert(1)</script>', '/'],
    // No leading slash — would parse against origin/ as same-origin but
    // the input-side check rejects.
    ['evil.com', '/'],
    ['?redirect=foo', '/'],
    // Empty.
    ['', '/'],
    // Whitespace stripped by the URL parser — would normalise to
    // `//evil.com` at navigation time. Regression for the second-pass
    // security-review finding.
    ['/\t/evil.com', '/'],
    ['/\r/evil.com', '/'],
    ['/\n/evil.com', '/'],
    ['/\t\t/evil.com', '/'],
    ['/\r\n/evil.com', '/'],
    ['\t//evil.com', '/'],
    // Path-traversal forms that the URL parser normalises to a
    // `//evil.com` pathname through `.`/`..` segment collapsing — same
    // origin per the parser, but `router.push('//evil.com')` would still
    // emit a protocol-relative navigation. Regression for the third-pass
    // security-review finding; caught by the output-side check.
    ['/.//evil.com', '/'],
    ['/..//evil.com', '/'],
    ['/././/evil.com', '/'],
    ['/foo/../..//evil.com', '/'],
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
