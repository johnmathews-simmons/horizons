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
 * Strategy: parse the input against the document origin using the URL
 * constructor — the same parser the browser uses for navigation — and
 * accept only if the parsed origin matches. This is a parser-equivalence
 * defence: a regex-based check has a differential against the URL
 * parser's whitespace stripping (`\t`, `\n`, `\r` are dropped by step 1
 * of the WHATWG basic URL parser, so `/\t/evil.com` would survive a
 * "doesn't start with //" regex but normalise to `//evil.com` at navigate
 * time). Going through `new URL(raw, origin)` closes the gap by routing
 * the validator and the navigator through the same parser.
 *
 * Returns `pathname + search + hash` so any host the parser found is
 * discarded — and re-asserts that the result itself starts with exactly
 * one '/' (not '//' or '/\'). The URL parser collapses `.` segments but
 * preserves empty segments, so `/.//evil.com` resolves to a same-origin
 * URL whose `pathname` is `//evil.com`; returning that to `router.push`
 * would still produce a protocol-relative navigation. The output check
 * closes that gap.
 */
export function sanitiseRedirect(raw: unknown): string {
  if (typeof raw !== 'string' || raw.length === 0) return '/'
  // Input-side check: the URL parser resolves bare strings ('evil.com',
  // '?q=1') as relative to `origin/`, which would be same-origin and
  // pass the origin check below. Demanding a leading '/' keeps the
  // policy "only same-origin paths the caller explicitly typed as
  // paths" rather than "anything the URL parser can normalise to same
  // origin".
  if (!raw.startsWith('/')) return '/'
  // Without a window (SSR / unusual jsdom configs), refuse rather than
  // attempt to validate without the document's origin to compare against.
  if (typeof window === 'undefined' || !window.location?.origin) return '/'

  let parsed: URL
  try {
    parsed = new URL(raw, window.location.origin)
  } catch {
    return '/'
  }
  if (parsed.origin !== window.location.origin) return '/'
  const out = parsed.pathname + parsed.search + parsed.hash
  // Output-side check: even though the parsed URL is same-origin,
  // `pathname` can still begin with `//` (e.g. `/.//evil.com` resolves
  // there). `router.push('//evil.com')` would protocol-relative navigate.
  if (!/^\/(?![\\/])/.test(out)) return '/'
  return out
}
