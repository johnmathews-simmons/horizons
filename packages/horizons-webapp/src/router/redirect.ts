/**
 * Coerce a `?redirect=` query value into a same-origin path.
 *
 * Vue Router 4's `router.push(string)` resolves the string with the same
 * URL semantics the browser would apply, so `//evil.com` or `/\evil.com`
 * become protocol-relative navigations to a different origin. The login
 * flow takes `?redirect=` straight from the URL bar, so an attacker can
 * craft a link like `/login?redirect=//evil.com` and exfiltrate the user
 * after authentication.
 *
 * Rule: accept only strings starting with exactly one '/' and not '//' or
 * '/\'. Anything else (absolute URLs, scheme-relative, backslash-prefixed,
 * non-strings, empty) falls back to '/'.
 */
export function sanitiseRedirect(raw: unknown): string {
  if (typeof raw !== 'string') return '/'
  if (!/^\/(?![\\/])/.test(raw)) return '/'
  return raw
}
