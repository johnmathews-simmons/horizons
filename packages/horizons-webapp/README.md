# horizons-webapp

The Horizons SPA. Vue 3 + TypeScript + Vite + Pinia + Vue Router + Vitest +
TanStack Vue Query, styled with Tailwind v4 + shadcn-style primitives.

This is a customer of the public REST API exposed by `horizons-api`. No
internal back-channel; the webapp talks to the same endpoints external
integrators do. See `docs/4. services.md` in the repo root.

The webapp is not a `uv` workspace member — it lives alongside the Python
packages under `packages/` but uses npm.

## Setup

```bash
cd packages/horizons-webapp
npm install
```

The API base URL is hardcoded to `http://localhost:8000` for development
(see `src/api/client.ts`). WU5.1 will replace this with a runtime
`/config.json` lookup so one bundle can ship to every environment.

## Scripts

```bash
npm run dev          # vite dev server
npm run build        # type-check + production build
npm run type-check   # vue-tsc
npm run test:unit    # vitest
npm run lint         # oxlint + eslint --fix (local dev)
npm run lint:check   # oxlint + eslint with no fixes (what CI runs)
npm run format       # prettier --write src/
```

## API client

`src/api/client.ts` is the single Axios instance every HTTP call goes
through. It carries `withCredentials: true` globally so the API's
`HttpOnly` refresh-token cookie (scoped to `/v1/auth`) rides on auth-flow
calls, and runs two interceptors:

- a request interceptor that injects `Authorization: Bearer <access>` from
  the auth bridge,
- a response interceptor that, on 401, decides per-bearer-kind: under an
  `access` bearer, calls `bridge.refresh()` (with single-flight coalescing
  across concurrent 401s), retries the original request once, and falls
  back to clearing the store + pushing `/login` if refresh itself fails;
  under an `impersonation` bearer, calls `bridge.onImpersonationExpired()`
  (which drops support view, surfaces a toast, and bounces back to
  `/admin/clients`) and rejects the original request. **The refresh
  cookie belongs to the original admin** — calling `/v1/auth/refresh`
  under an impersonation bearer would silently re-elevate to admin
  context while the SPA still rendered support view. See WU5.4 journal
  adversary class 6.

`setAuthBridge({ getAccessToken, getKind, refresh, onAuthFailure, onImpersonationExpired })`
is the seam — `main.ts` wires it after `createPinia()` so `client.ts`
has no static dependency on the store or router.

## Auth

`src/stores/auth.ts` is a Pinia store holding the access token **in
memory only** (`ref<string | null>`) — never `localStorage` /
`sessionStorage` / `IndexedDB`. The refresh token is the API's `HttpOnly`
cookie; JS can never see it. See `docs/api/auth.md` for the wire
contract.

The store also tracks `kind: 'access' | 'impersonation'` for the bearer
currently held, the parsed `MeResponse` principal (role, email,
subscription summary), and — when in support view — an
`impersonationState` snapshot containing the target's email, the original
admin's email + access token, and the entry / expiry timestamps. The
snapshot is in memory only too: a page reload destroys it and the
cookie-driven cold bootstrap re-enters the SPA as the original admin,
never as the impersonated client.

## Routes

`src/router/index.ts` declares public + client routes (`/login`, `/`,
`/changes`, `/changes/:id`, `/watchlists`) and the admin subtree
(`/admin/*`, see `AdminLayout`) — `/admin` and its children carry
`meta: { requiresAuth: true, requiresAdmin: true }` and the guard
redirects non-admin (or mid-impersonation) callers to `/`. A global
`beforeEach` redirects unauthenticated visits to `/login?redirect=<from>`
and bounces already-authenticated `/login` hits to `/admin/clients` for
admins and `/` for clients. The `?redirect=` query is sanitised through
`src/router/redirect.ts` before `router.push` — see that file's header
for the open-redirect threat model and the input, same-origin, and
output-side checks it applies.

## Admin views + support view

`/admin/clients`, `/admin/clients/:id`, and `/admin/audit` are the
operator surfaces. The detail page exposes a subscription editor (add /
remove scopes) — scope removal opens a confirmation modal listing the
documents that would be soft-hidden — and an "Enter support view"
button that calls `POST /v1/admin/impersonate`. On a 201 response the
auth store swaps to an `impersonation` bearer, captures the admin's
original access token in memory, and `App.vue` renders the
`SupportViewBanner` over every route until exit. `document.title` is
prefixed `[SUPPORT] ` for the lifetime of the impersonation. There is
no `/v1/admin/impersonate/exit` endpoint; exit is purely client-side
(drop the in-memory token, restore the admin snapshot, navigate back
to `/admin/clients`). The 15-minute token TTL bounds the elevation
window; a 401 on a request made under the impersonation bearer routes
through `onImpersonationExpired` rather than the cookie-based refresh,
so the admin is never silently re-elevated while the UI still believes
it is in support view.
