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
- a response interceptor that, on 401, calls `bridge.refresh()` (with
  single-flight coalescing across concurrent 401s), retries the original
  request once, and falls back to clearing the store + pushing `/login`
  if refresh itself fails.

`setAuthBridge({ getAccessToken, refresh, onAuthFailure })` is the seam —
`main.ts` wires it after `createPinia()` so `client.ts` has no static
dependency on the store or router.

## Auth

`src/stores/auth.ts` is a Pinia store holding the access token **in
memory only** (`ref<string | null>`) — never `localStorage` /
`sessionStorage` / `IndexedDB`. The refresh token is the API's `HttpOnly`
cookie; JS can never see it. See `docs/api/auth.md` for the wire
contract.

## Routes

`src/router/index.ts` declares `/login` (public) and `/`
(`requiresAuth: true`). A global `beforeEach` guard redirects
unauthenticated visits to `/login?redirect=<from>` and bounces
already-authenticated `/login` hits to `/`. The `?redirect=` query is
sanitised through `src/router/redirect.ts` before `router.push` — see
that file's header for the open-redirect threat model and the input,
same-origin, and output-side checks it applies.
