# 2026-06-05 — WU5.0: Vue app shell + auth store + routing

First Track-5 unit. Stands up the SPA shell against the auth surface that
WU4.2 + the WU4.2 security hardening landed. Worktree
`wu5.0-vue-shell-and-auth-store`.

## What shipped

### HTTP client (`src/api/client.ts` + `src/api/auth.ts`)

- `apiClient` is a single Axios instance configured with `withCredentials:
  true` (so the `HttpOnly` `refresh_token` cookie scoped to `/v1/auth` rides
  on refresh / logout calls) and `Accept: application/json`.
- API base URL is hardcoded to `http://localhost:8000` for now with a
  `TODO(WU5.1)` marker — WU5.1 replaces this with a runtime `/config.json`
  lookup. The single-line change is documented under "Follow-up wire-up"
  below.
- Request interceptor injects `Authorization: Bearer <access_token>` from
  the auth bridge on every call; deletes the header when no token is held.
- Response interceptor handles the documented refresh-on-401 dance:
  - On a 401 to any non-auth-flow endpoint, calls `bridge.refresh()` and
    retries the original request **once**. The retry is marked with a
    transport-only `_retried` flag so a second 401 cannot loop.
  - Auth-flow endpoints (`/v1/auth/login`, `/v1/auth/refresh`,
    `/v1/auth/logout`) carry `_skipAuthRefresh: true` so the interceptor
    leaves them alone. The flags live behind a module-augmentation block at
    the top of `client.ts`; the augmentation has to use `<D = any>` (matching
    axios's upstream signature) or TS treats it as a conflicting interface
    instead of merging.
  - **Single-flight refresh**: if multiple requests 401 concurrently they
    share one `bridge.refresh()` promise via an `inFlightRefresh` slot. No
    refresh storms.
  - If `bridge.refresh()` itself rejects, the interceptor calls
    `bridge.onAuthFailure()` (which clears the store and pushes `/login`)
    and re-raises. A second 401 *after* a successful refresh is surfaced to
    the caller without re-entering refresh — `onAuthFailure` does not fire,
    avoiding double-clear races.
- `setAuthBridge({ getAccessToken, refresh, onAuthFailure })` is the seam.
  `main.ts` wires the bridge after `createPinia()` + the router instance
  are constructed, so `client.ts` has zero static dependency on either —
  no circular imports.

### Auth store (`src/stores/auth.ts`)

Composition-style Pinia store, `useAuthStore`:

- `accessToken` — a plain `ref<string | null>`. **In memory only**. Never
  touches `localStorage` / `sessionStorage` / `IndexedDB` per the locked-in
  decision. The refresh token is the API's `HttpOnly` cookie; JS can never
  see it.
- `isAuthenticated` — derived from `accessToken !== null`.
- `login({ email, password })` — `POST /v1/auth/login` with
  `X-Client-Type: browser` so the API sets the cookie + returns just
  `access_token` in the body.
- `refresh()` — `POST /v1/auth/refresh` (cookie-sourced, per the WU4.2
  security hardening: shape is now bound to the cookie, not to the header,
  so we do NOT send `X-Client-Type` here).
- `logout()` — `POST /v1/auth/logout`, then always clears the token (the
  store still clears even if the network call fails so we never leave the
  UI in a half-authenticated state).
- `clear()` / `setAccessToken()` — internal helpers exposed for the bridge
  and for tests.

### Routes + navigation guard (`src/router/index.ts`)

- History mode (`createWebHistory`).
- `/login` (public) and `/` (`requiresAuth: true`).
- Global `beforeEach` guard:
  - Auth-required route + unauthenticated → redirect to
    `/login?redirect=<from>`.
  - `/login` + already authenticated → bounce to `/`.

### UI shell (`src/views/LoginView.vue`, `src/views/HomeView.vue`,
`src/components/ui/*`)

- shadcn-style primitives manually scaffolded under `src/components/ui/`
  (`Button`, `Input`, `Label`) plus the `cn()` utility at `src/lib/utils.ts`.
  We did NOT run `npx shadcn-vue@latest init` because it requires the
  `@/*` import alias in the root `tsconfig.json`, but in this repo aliases
  live in the project-referenced `tsconfig.app.json` instead — re-shaping
  the tsconfig layout would have been a bigger change than the three
  components warrant. The hand-scaffolded files mirror the upstream
  registry's output for `--style nova --base-color neutral`. If we want the
  full shadcn-vue tooling later, WU5.2/5.3 can add the alias to the root
  tsconfig and re-run init then.
- `LoginView` is a generic-copy "Sign in to Horizons" form. Submit invokes
  the auth store; on 401 it renders a generic "Invalid email or password"
  message; on success it pushes to `?redirect` or `/`.
- `HomeView` is a placeholder shell with a sign-out button calling
  `auth.logout()`. Real views land in WU5.2 (watchlists) and WU5.3 (change
  browsing + clause diff).

### TanStack Vue Query

`VueQueryPlugin` registered at the top level in `main.ts`. No queries are
wired yet; that's WU5.2/5.3 work.

### Tests (`vitest` + `msw`)

`src/test/server.ts` exports a shared `setupServer()` instance; tests
register per-test handlers via `server.use(...)`. Global `beforeAll` /
`afterEach` / `afterAll` in `src/test/setup.ts`, registered via
`vitest.config.ts → test.setupFiles`. 16 tests across four suites, all
green:

- `src/api/__tests__/client.spec.ts` — bearer injection, single retry on
  401, second-401-after-refresh surfaces to caller, refresh-rejection
  triggers `onAuthFailure`, single-flight coalescing under concurrent 401s.
- `src/stores/__tests__/auth.spec.ts` — login populates token + sends
  `X-Client-Type: browser`, logout clears token, refresh swaps token, 401
  on login leaves store unauthenticated.
- `src/views/__tests__/LoginView.spec.ts` — submit calls store + navigates
  home, 401 renders error + stays on login, generic copy (no firm names),
  and a sanity check that the real store is in use (not a hoisted mock).
- `src/router/__tests__/guard.spec.ts` — unauthenticated `/` → `/login`
  with `redirect` query, authenticated `/` stays home, authenticated
  `/login` bounces home.

## Verification gate

From the worktree:

```bash
# Python sweep — no Python touched, but the gate per CLAUDE.md
uv run ruff check .          # all checks passed
uv run pyright               # 0 errors, 22 warnings (pre-existing stub warnings)
uv run pytest -m "not integration"   # 319 passed, 4 skipped (pre-existing)
uv run pre-commit run --all-files    # passed (ruff-format wants to collapse
                                     # one comprehension in test_otel.py from
                                     # a Track 7 file we don't own this WU;
                                     # reverted so the change stays out of our
                                     # commit. Track 7's session will pick it
                                     # up when they run their own pre-commit.)

# Webapp sweep (the real gate)
cd packages/horizons-webapp
npm run lint:check           # oxlint + eslint, no fixers; clean
npm run test:unit -- --run   # 4 files, 16 tests, all pass
npm run build                # vue-tsc + vite build, 0 TS errors
```

Build emits two Rolldown `INVALID_ANNOTATION` warnings from
`@vueuse/core/dist/index.js` about misplaced `/* #__PURE__ */` comments.
These are third-party warnings, not our code, and the build still finishes
green (`✓ built in ~400ms`). They'd be resolved by `@vueuse/core` adjusting
their annotation placement; nothing to fix on our side.

## Decisions made beyond the prompt

1. **`X-Client-Type: browser` only on login**, not on refresh/logout. This
   is the WU4.2 security-hardening contract from
   `260605-wu42-securityfix-auth-hardening.md`: refresh/logout response
   shape is bound to the cookie source, not to the client-controlled
   header. Sending the header would have been harmless but confusing — the
   server ignores it. We picked "don't send it" so the auth store's
   refresh/logout calls match the documented browser flow literally.
2. **Auth bridge via `setAuthBridge(...)`** rather than direct import of
   the store from the client module. Avoids the circular import that
   would otherwise form (`client → store → client`) and keeps the client
   testable against a hand-crafted bridge without spinning up Pinia.
3. **`logout()` clears the token in a `finally`**. The HTTP call can fail
   (network gone, server gone, 401 because refresh expired in the
   background), but the user clicked "Sign out" — leaving them visually
   "signed in" because the API call failed would be hostile. Server-side
   the access token expires in 15 min anyway and the cookie's `jti` is
   the only thing that can still be replayed.
4. **shadcn-vue init skipped, components hand-scaffolded.** Reason
   documented in the UI shell section. The output matches what
   shadcn-vue init would have produced (`Button`, `Input`, `Label` +
   `cn()` + the same Tailwind class shapes); skipping the CLI avoided
   restructuring the tsconfig references.
5. **`vue/multi-word-component-names` disabled for
   `src/components/ui/**/*.vue`**. Same rationale as upstream shadcn:
   the UI primitives keep single-word names to mirror the registry.
   Scoped override in `eslint.config.ts`; the rule still fires on app
   views (HomeView, LoginView are already multi-word so unaffected).
6. **Augmentation lives inline in `client.ts`**, not in a separate
   `.d.ts`. Tried two .d.ts placements first (`src/api/axios.d.ts`,
   `src/types/axios-augmentations.d.ts`) but vue-tsc's `--build` flow
   didn't merge them with the upstream axios types — the augmentation
   was being treated as a fresh, conflicting interface. Inlining the
   `declare module 'axios'` block at the top of the file that uses the
   flags makes the merge reliable.
7. **`_skipAuthRefresh` flag rather than a separate axios instance for
   auth endpoints.** A separate instance would have meant duplicating
   `withCredentials` / `baseURL` / `Accept` config and remembering not to
   diverge them. A transport-only flag scoped to a few call-sites was
   cheaper.

## Follow-up wire-up

WU5.1 (runtime `/config.json`) will displace the hardcoded base URL in
`packages/horizons-webapp/src/api/client.ts`. The change is one line:

```diff
-// TODO(WU5.1): replace with runtime /config.json lookup. A single bundle ships
-// to every environment; the base URL is read from a config file fetched at
-// app boot, not baked in here.
-const API_BASE_URL = 'http://localhost:8000'
+import { getRuntimeConfig } from '@/config'
+const API_BASE_URL = getRuntimeConfig().apiBaseUrl
```

…and a corresponding wait in `main.ts` to fetch `/config.json` before
constructing the axios instance (or a getter that lazily resolves on first
request — TBD as part of WU5.1's brief).

WU5.6 (webapp CI) will pick up the now-existing
`npm run lint:check && npm run test:unit -- --run && npm run build` gate
and lift it into `.github/workflows/ci.yml`. The existing `webapp.yml`
already does this for the scaffold; WU5.6 will extend coverage to the new
files.

## Manual smoke-test (for the user)

The in-session verification gate covers the unit-test surface; the
end-to-end "does it actually log in against the API" check is a manual
step.

1. **Run a migration + start the API**, in terminal 1:

   ```bash
   cd /Users/john/projects/syncthing/agent-lxc/horizons
   uv run alembic upgrade head   # if migrations haven't been applied
   uv run uvicorn horizons_api.app:app --reload --port 8000
   ```

2. **Seed a user.** The auth-endpoint integration tests
   (`tests/test_auth_endpoints.py`) construct users via the
   `seed_user(...)` helper. For a real local-dev login, drop into a Python
   REPL with the same DB URL the API uses and seed by hand:

   ```python
   # uv run python
   import asyncio
   from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
   from horizons_core.core.auth import hash_password
   from horizons_core.db.session import unauthenticated_session, bind_app_user_id
   from horizons_core.repos.users import UsersRepository  # adjust path
   # …insert a row into users with role='client', password_hash=hash_password('hunter2')
   ```

   *Open item: there is no first-class `scripts/create_user.py` yet. WU4.5
   will add the admin CLI for client provisioning. Until then, the
   integration-test fixture is the canonical recipe — copy its pattern.*

3. **Start the webapp**, in terminal 2:

   ```bash
   cd /Users/john/projects/syncthing/agent-lxc/horizons/packages/horizons-webapp
   npm run dev
   ```

4. **Open** `http://localhost:5173/login` in a browser. Vite serves the
   SPA; the global guard will redirect `/` to `/login` automatically if
   you start at `/`.

5. **Sign in** with the seeded email + password. Expected:
   - The POST to `http://localhost:8000/v1/auth/login` carries
     `X-Client-Type: browser`.
   - The 200 response sets `refresh_token` as `HttpOnly; Secure;
     SameSite=Lax; Path=/v1/auth` and returns `{ access_token: "..." }`
     in the body (no refresh token in the body).
   - The webapp navigates to `/` and renders the placeholder Home view.

6. **Sign out.** Expected:
   - POST to `/v1/auth/logout` with no `Authorization` header and the
     cookie attached automatically (you can verify in DevTools → Network).
   - The cookie is cleared via `Set-Cookie: refresh_token=; Max-Age=0`.
   - The webapp pushes back to `/login`.

7. **Refresh-on-401 sanity (optional).** Open DevTools and set the
   access-token expiry to "now" by clearing `accessToken` in the Pinia
   store (Vue Devtools → Pinia → auth → set `accessToken: null`). Trigger
   any data call from a view. Expected: the interceptor will see the 401,
   call `/v1/auth/refresh` (cookie-sourced), get a new access token, and
   retry. There's no data call to trigger yet — WU5.2 lands one — so
   this step is informational until then.

## Status by suite

- Webapp: 16 tests passing, lint clean, build clean (0 TS errors).
- Python: 319 unit tests passing (no Python touched in this WU; the gate
  is run defensively per the prompt).

## Track 5 status

| WU | Status |
| --- | --- |
| **WU5.0** | **shipped (axios client, Pinia auth store, router guard, login UI)** |
| WU5.1 | next — runtime `/config.json` |
| WU5.2 | depends on WU4.3 + WU5.0 — watchlist view |
| WU5.3 | depends on WU4.4 + WU5.0 — change-browsing + clause diff |
| WU5.4 | depends on WU4.5 + WU5.2 — admin views |
| WU5.5 | depends on WU5.3 — large-doc rendering safety |
| WU5.6 | depends on WU5.0 — webapp CI build + lint |

## Cadence note

Worktree `wu5.0-vue-shell-and-auth-store`. Fast-forward merge into `main`
per `CLAUDE.md`'s CI / merge cadence.
