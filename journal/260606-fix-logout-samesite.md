# Logout 401 → SameSite=None for the refresh cookie

*Last revised: 2026-06-06.*
*Path: journal/260606-fix-logout-samesite.md.*

## Reported symptom

Clicking the **Sign out** button on the deployed SPA cleared the in-memory
session (data fetches stopped working) but the page did not navigate —
the user remained visually on the authed view until a manual reload, at
which point they landed on `/login`.

## What I jumped to first (and shouldn't have)

I read the code, formed a hypothesis (`auth.logout()` uses `try/finally`
with no `catch`; if `logoutRequest()` rejects, the finally clears
in-memory state but the rejection propagates to `onSignOut`, so
`router.push({ name: 'login' })` never runs), and patched the store to
swallow the error. The patch is defensible — client-side logout should
be best-effort — but it papered over the symptom without confirming
the cause. The systematic-debugging skill's Phase 1 ("reproduce
consistently, gather evidence") was the step I skipped.

## What the Network tab showed

After asking, the user opened DevTools and clicked logout:

```
POST horizons-dev-api.prouddune-…westeurope.azurecontainerapps.io/v1/auth/logout
→ 401  (47 B response, 22 ms)
```

So `logoutRequest()` really was throwing — the hypothesis was right —
but the deeper question was *why* a fresh, valid session 401'd on
logout.

## Root cause

The deployed SPA lives on a different *site* from the API: the SPA is
served from Front Door / Storage `$web`; the API is on the Container
Apps default host (`*.westeurope.azurecontainerapps.io`). Those are
not the same site under the SameSite cookie rules.

`packages/horizons-api/src/horizons_api/routes/auth.py:118-127` set
the refresh cookie with `samesite="lax"`. Under `Lax`, the browser
sends the cookie on cross-site **top-level navigations** but withholds
it on cross-site **XHR/fetch**, even when `withCredentials: true` is
set on the axios client. So `apiClient.post('/v1/auth/logout')` ran
without the `refresh_token` cookie. The backend's
`require_refresh_principal` dependency had no token to verify and
returned 401, which propagated up through the SPA call chain.

This was a broader bug than just logout. The same condition breaks:

1. **Logout** — backend returns 401, store rethrew, router never
   pushed.
2. **Cold-bootstrap refresh on reload** — the router guard at
   `packages/horizons-webapp/src/router/index.ts:82-91` calls
   `auth.refresh()` once on first navigation to restore an active
   session from the cookie. With the cookie withheld, this always
   fails on the deployed SPA, forcing a re-login after every reload
   even when the refresh-token TTL has weeks left.

Day-to-day API calls were unaffected because they ride on the
in-memory access token in the `Authorization` header, not on the
cookie. Login itself works because it carries `X-Client-Type:
browser` in the request body and the cookie is *set* by the response
(`Set-Cookie` on a cross-site request is allowed; sending it back on a
subsequent cross-site XHR is what `SameSite=Lax` blocks).

## Fix

Two changes shipped together in `2632ee8`:

1. `packages/horizons-api/src/horizons_api/routes/auth.py` —
   `_set_refresh_cookie` and `_clear_refresh_cookie` now set
   `samesite="none"`. `Secure=True` and `HttpOnly=True` remain;
   `SameSite=None` is only allowed alongside `Secure`, so the
   prerequisite is satisfied. `Path=/v1/auth` continues to scope the
   cookie to the three auth-flow endpoints.

2. `packages/horizons-webapp/src/stores/auth.ts` — `auth.logout()`
   now catches and `console.warn`s on `logoutRequest()` failure
   instead of rethrowing. Navigation to `/login` is no longer gated
   on a successful server-side revocation. The warn is deliberate: a
   silent swallow would have hidden this exact regression, so a future
   logout failure leaves a breadcrumb in the console.

3. `packages/horizons-webapp/src/stores/__tests__/auth.spec.ts` — the
   existing test named "logout clears the access token even when the
   network call fails" was mocking `/logout` as a 204 success, so it
   only verified the happy path. The mock now returns 401, and the
   test asserts `auth.logout()` resolves (not rejects).

## CSRF trade-off

Switching to `SameSite=None` removes one layer of CSRF defence-in-depth.
What's still in place:

1. `HttpOnly` — JavaScript on a malicious page cannot read the cookie.
2. `Secure` — cookie only ever travels over HTTPS.
3. `Path=/v1/auth` — cookie is not attached to `/v1/me`, the data
   plane, or any non-auth endpoint, so a CSRF would have to target one
   of three explicit auth endpoints.
4. The three cookie-consuming endpoints (`login`, `refresh`, `logout`)
   are all `POST`. Login takes a JSON body with credentials in it, so
   a CSRF login is pointless. Refresh would rotate the victim's token
   to a value the attacker doesn't see (the new cookie goes back to the
   victim's browser). Logout would log the victim out — annoying but
   not an exfiltration.
5. The data plane is bearer-auth-only via in-memory access token, with
   no cookie attached.

Post-demo, the cleaner fix is to put the API behind Front Door at a
sibling subdomain of the SPA's domain (e.g. `app.example` +
`api.example`) so they're same-site under cookie rules, and revert to
`SameSite=Lax`. That's an IaC change and not safe to land two days
before the demo.

## What I'd do differently

1. The user's report described a navigation-not-happening symptom but
   was ambiguous about *why* — UI bug or backend bug? I should have
   asked for the Network tab before writing any code. The patch I
   shipped on the first pass would have hidden the deeper backend bug
   if shipped alone; I only escalated when the user pushed back with
   "is it a good fix?"
2. The webapp test's name ("even when the network call fails") was
   factually wrong relative to its mock for who knows how long.
   Test-name-to-mock-shape drift is the kind of thing a pre-commit
   hook can't catch. Manual review during code review didn't catch
   it either. No process fix here — flagging it as a known failure
   mode for the project.

## Verification

1. API unit tests: 8 passed (`pytest -k "auth or refresh or logout or
   login" -m "not integration"`).
2. Webapp unit tests: 168 passed (`npm run test:unit -- --run`).
3. Lint + typecheck: clean on both sides.
4. Pre-commit: clean.

End-to-end verification needs a redeploy of the API container. Two
checks to run post-deploy:

1. Sign in → click sign out → URL flips to `/login` without reload.
2. Sign in → reload an authed page (e.g. `/changes`) → stays signed
   in (cold-bootstrap refresh now works) instead of bouncing to
   `/login`.

If (2) still bounces, there is a separate bug — open another entry.

## Status

Shipped as commit `2632ee8` on `main`. Awaiting redeploy to take
effect on `horizons-dev`.
