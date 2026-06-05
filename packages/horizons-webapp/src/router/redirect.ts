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
 * discarded even if the same-origin check were to pass.
 */
export function sanitiseRedirect(raw: unknown): string {
  if (typeof raw !== 'string' || raw.length === 0) return '/'
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
  // Reject anything that didn't start with a path — `//evil.com` parses
  // successfully against same-origin under some hosts and we want a
  // belt-and-braces check on the leading byte too.
  if (!raw.startsWith('/')) return '/'
  return parsed.pathname + parsed.search + parsed.hash
}
