# 2026-06-06 — WU5.4: admin views + support view

*Last revised: 2026-06-06.*
*Path: journal/260606-wu54-admin-views-support-view.md.*

The last Track-5 work unit. Stands up the operator surface — `/admin`,
`/admin/clients`, `/admin/clients/:id` (with a subscription editor and an
"Enter support view" affordance), and `/admin/audit` — and the impersonation
flow that lets an admin reproduce a client's view of the SPA. Built on top
of WU4.7's `GET /v1/admin/clients` and `POST /v1/admin/impersonate`, WU4.5's
`/v1/admin/subscriptions`, WU7.4's `GET /v1/admin/audit`. Worktree
`worktree-wu5.4-admin-views-support-view`.

This is the third application of the
[secfix-pattern retrospective](./260605-secfix-pattern-retrospective.md)'s
named-adversary framing — and the first to land in the SPA layer where the
adversaries are operator-side (admin forgets they're impersonating, admin
fat-fingers a scope removal) rather than purely server-side.

## What shipped

### Auth-store extensions (`src/stores/auth.ts`)

The Pinia store grew three new pieces of state:

- `kind: 'access' | 'impersonation'` on the bearer.
- `principal: MeResponse | null` — the cached `/v1/me` envelope so route
  guards and views can read role / email without hitting the network.
- `impersonationState: ImpersonationState | null` — the snapshot needed to
  render the support-view banner AND restore the original admin context
  on exit: target email, original admin email, the original access token,
  the original principal, plus entry / expiry timestamps.

Two new actions:

- `enterSupportView(targetUserId, reason?)` — refuses unless the caller is
  an authenticated admin; POSTs `/v1/admin/impersonate`; on the 201
  response captures the snapshot and swaps the bearer + kind to
  `impersonation`. On non-201 the store is left untouched (see
  [[adversary class 5]] below). The synthesised post-swap principal is
  client-shaped so all `/me`-driven views (Home, Watchlists, Changes)
  render the client's view, not the admin's.
- `exitSupportView()` — purely client-side, no API call. Restores the
  captured original access token + kind + principal and clears
  `impersonationState`. The 15-minute API-side TTL is the durable bound;
  a server-side `/v1/admin/impersonate/exit` would write a row that
  carries no signal the entry row doesn't already carry (this matches the
  WU4.7 decision, recorded here for completeness).

`login()` and `refresh()` were updated to call `refreshPrincipal()` after
landing a fresh access token so the role gate has something to read on a
cold bootstrap.

### Refresh-interceptor changes (`src/api/client.ts`)

The `AuthBridge` interface grew two members:

- `getKind(): 'access' | 'impersonation'` — read at response time so the
  interceptor knows whether to refresh.
- `onImpersonationExpired(): void` — called when a 401 fires under an
  impersonation bearer. The bootstrap wires this to: drop impersonation
  state via `exitSupportView()`, surface an error toast ("Support view
  expired"), and bounce to `/admin/clients`.

The interceptor's 401 path now branches on `kind`:

- `access` — single-flight refresh + retry, same as before.
- `impersonation` — **never** call `/v1/auth/refresh`. The cookie belongs
  to the original admin; firing refresh would silently re-elevate the
  bearer to admin context while the SPA still rendered support view. See
  [[adversary class 6]] below.

### Route guard + admin layout

`/admin` and its children — `/admin` (dashboard), `/admin/clients`,
`/admin/clients/:id`, `/admin/audit` — share an `AdminLayout` and carry
`meta: { requiresAuth: true, requiresAdmin: true }`. The router's
`beforeEach` redirects non-admin callers to `/`, and the "already
authenticated" landing on `/login` now forwards admins to
`/admin/clients` (clients still land on `/`).

A mid-impersonation admin has `kind='impersonation'`, `role='client'`,
`isAdmin === false` — the guard treats them the same as a real client.
Re-entering `/admin/*` requires exiting support view first. This was a
deliberate design choice: a UI that lets the impersonator step partly
in / partly out of support view would muddy the operator-mode framing
the amber banner is trying to make unambiguous.

### `SupportViewBanner` + tab-title hook

The banner is part of `App.vue`'s layout root, NOT a child of
`AdminLayout` or any specific view — so it persists across every route
the admin navigates during support view. `bg-amber-500` with a high-
contrast amber-50/900 inner pill on the Exit button; `role="status"` +
`aria-live="polite"` so screen readers announce entry / exit; both
target email and original admin email are surfaced ("viewing
client@example.test as admin@example.test"). The Exit button calls
`auth.exitSupportView()` then `router.push({ name: 'admin-clients' })`.

`useSupportViewTitle()` is a side-effect composable mounted once in
`App.vue` — it `watchEffect`s on `auth.impersonationState` and keeps
`document.title` prefixed with `[SUPPORT] ` for the lifetime of the
impersonation, removing the prefix on exit. The tab title is the
defence-in-depth signal for the case where the amber banner doesn't
render (RTL, narrow viewport, screen reader, CSS override).

### Admin views

`AdminClientsView` is a table-of-clients backed by `GET /v1/admin/clients`
with limit/offset pagination via Previous / Next buttons + a "Page X of
Y · N total" indicator. Each row opens `/admin/clients/:id`.

`AdminClientDetailView` shows the active subscription's scopes, an
inline "Add scope (jurisdiction + sector)" form that PATCHes
`add_scopes`, and per-scope Remove buttons that open a
`ScopeRemovalConfirmDialog` listing the documents in the admin's
discovery feed that fall inside the scopes being removed. Confirming
PATCHes `remove_scopes`; the response's `watchlists_soft_hidden` count
is surfaced in the success toast. A sidebar carries the "Enter support
view" affordance.

`AdminAuditView` reads `GET /v1/admin/audit` with default `since = now -
24h` and exposes filter inputs for `admin_id`, `target_user_id`,
`action` (operator | impersonation). Operator and impersonation rows
get visually distinct pills + a subtle amber row-tint on impersonation
rows so the eye picks them out at scan speed.

## Six adversary classes & their defences

Per the [secfix-pattern retrospective](./260605-secfix-pattern-retrospective.md)'s
named-adversary framing. The five classes the retrospective named, plus
the refresh-interceptor invariant added in the WU5.4 brief.

1. **Admin enumerating client identifiers without leaving a trail.**
   *Defence:* every `GET /v1/admin/clients` request runs through the
   API's `admin_operator_session_for_request`, which writes one
   `admin_access_log` operator-mode row *before* yielding the session.
   The SPA cannot bypass — the audit row write is unconditional.
   *Pinned by:* `tests/api/test_admin_clients_endpoint.py::test_clients_list_writes_one_operator_audit_row_per_request`
   (server-side; WU4.7). No SPA-side test required because the SPA only
   calls the endpoint — there is no implementation choice it can make
   here.

2. **Admin fat-fingers subscription scope removal, silently breaking a
   client's watchlist visibility.** *Defence:* the per-scope Remove
   button opens `ScopeRemovalConfirmDialog`, which lists every document
   in the admin's discovery feed whose `(jurisdiction, sector)` falls
   inside the scope(s) being removed. Cancel / X / outside-click /
   Esc all close the dialog WITHOUT emitting `confirm`; only the
   explicit "Remove scopes" button calls the mutation. The API's
   response includes `watchlists_soft_hidden`, surfaced in the success
   toast so the admin sees the impact landed.
   *Pinned by:*
   `src/components/admin/__tests__/ScopeRemovalConfirmDialog.spec.ts`
   ("Cancel closes the dialog and does NOT emit confirm",
    "explicit Remove button emits confirm exactly once",
    document listing match)
   and `src/views/__tests__/AdminClientDetailView.spec.ts`
   ("Remove scope: opens the confirm dialog. Cancel does NOT send
   PATCH; explicit confirm sends remove_scopes and surfaces the
   soft-hidden count").

3. **Support-view banner fails to render under a CSS edge case (RTL,
   narrow viewport, screen reader, CSS override) — admin forgets
   they're impersonating.** *Defence:* layered:
   - The banner lives in `App.vue`'s layout root, not a modal, not a
     route view.
   - `role="status"` + `aria-live="polite"` so SR announces entry / exit.
   - `bg-amber-500` with an amber-900-on-amber-50 inner Exit pill — the
     defining text + button work even if the background fails.
   - `document.title` is prefixed `[SUPPORT] ` via `useSupportViewTitle`
     so the tab signal is the same regardless of CSS state.

   *Pinned by:*
   `src/components/admin/__tests__/SupportViewBanner.spec.ts`
   (DOM presence gated by `impersonationState`, `role="status"`,
   `aria-live="polite"`, `bg-amber-500` class present)
   and the `useSupportViewTitle` test in the same file
   (`[SUPPORT] ` prefix appears on entry, removed on exit).

4. **Admin closes browser tab in support view, reopens — re-enters as
   client silently.** *Defence:* `impersonationState`, `kind`, and
   `accessToken` all live in a Pinia `ref()` — JS heap only. No
   `localStorage`, no `sessionStorage`, no IndexedDB, no cookies (the
   only persistent auth artefact is the API's `HttpOnly`
   `refresh_token` cookie, which belongs to the original admin and was
   captured BEFORE entry). A real reload destroys the JS heap; the
   cookie-driven cold-bootstrap in the router's `beforeEach` then calls
   `/v1/auth/refresh`, gets the admin's access token, and the SPA
   re-enters as the original admin.
   *Pinned by:*
   `src/stores/__tests__/auth-impersonation.spec.ts`
   ("a fresh store (== page reload) does NOT carry impersonation
   state, and no impersonation token is recoverable from any browser
   storage") — simulates reload by replacing the Pinia instance and
   sweeps `localStorage` + `sessionStorage` for the token string.

5. **Network blip between support-view enter and the audit-log write
   leaves admin impersonating with no audit row.** *Defence:* the API's
   `POST /v1/admin/impersonate` writes the impersonation audit row
   inside the same transaction as the token mint (WU4.7); the SPA
   enters support view ONLY after the 201 response is parsed. A
   pre-201 failure — 4xx, 5xx, network error — keeps `accessToken`,
   `kind`, `principal`, and `impersonationState` at their pre-call
   values, surfaces an error toast, and leaves the admin on
   `/admin/clients/:id` so they can retry.
   *Pinned by:*
   `src/stores/__tests__/auth-impersonation.spec.ts`
   ("POST /v1/admin/impersonate failure leaves the store unchanged")
   and `src/views/__tests__/AdminClientDetailView.spec.ts`
   ("POST /v1/admin/impersonate 500 keeps the SPA on the detail page
   and does NOT enter support view").

6. **Refresh interceptor masks an expired impersonation token by
   silently re-elevating to admin.** *Defence:* the
   `client.ts` interceptor consults `bridge.getKind()` BEFORE deciding
   whether to refresh. When `kind === 'impersonation'`, it calls
   `bridge.onImpersonationExpired()` (which clears impersonation
   state, restores the admin's bearer from the in-memory snapshot,
   surfaces a "Support view expired" toast, and pushes
   `/admin/clients`) and rejects the original request. `refresh()` is
   never called — so the admin's `refresh_token` cookie cannot be
   used to mint a fresh access bearer while the SPA still believes
   it is in support view.
   *Pinned by:*
   `src/api/__tests__/client-impersonation.spec.ts`
   ("does NOT call /v1/auth/refresh when kind is impersonation and a
   401 fires") + the regression net "still calls refresh when kind is
   access on 401" so a future refactor that drops the kind check is
   caught by either spec.

A non-class regression net: the existing
`src/api/__tests__/client.spec.ts` was updated to thread the new
`getKind` + `onImpersonationExpired` bridge members through every
mock — proving the `access`-bearer paths (bearer injection, single
retry, second-401 surfaces, refresh-rejection, single-flight)
unchanged.

## Second-review pass

After the implementation was complete and all 168 tests green, ran a
second-pass adversarial review against each of the six classes. The
review surfaced three items; the first was a real gap and was fixed
before declaring done. The other two are noted limits, not flaws.

### Real gap (fixed): the integration test missed `/admin/audit` route

While composing the integration test (admin login → /admin/clients →
enter support view → navigate /changes → guard refuses /admin → exit),
the test failed in CI with `No match for { name: 'admin-audit' }` —
because `AdminLayout`'s nav bar carries a `RouterLink :to="{ name:
'admin-audit' }"`. The first version of the test routes only stubbed
`admin-clients` + `admin-client-detail`. **Fixed** by adding the
`audit` child route to the integration test's router. The bug surfaced
only because the integration test mounts `AdminLayout` directly; the
per-view specs mount the view in isolation and never hit the RouterLink.

### Noted limit 1: `document.title` watcher is the sole title writer

`useSupportViewTitle` uses `watchEffect(() => { ... })` on
`auth.impersonationState`. If a different module mutates `document.title`
while impersonating (e.g. a future per-route title plugin), the
`[SUPPORT] ` prefix is lost until the next `impersonationState` change
re-fires the watcher. **No active risk** — no other code in the app
sets `document.title` today. If we add per-route titles post-demo, the
plugin should call into a single title-writer composable that re-applies
the prefix when impersonating. Captured here so a future agent knows the
invariant.

### Noted limit 2: `enterSupportView` does not collect a reason from the UI

The API accepts an optional `reason` query/body field that ends up in
the audit row's `reason` column. The current AdminClientDetailView
button calls `auth.enterSupportView(props.id)` without a reason — the
audit row records `null`. Adding a 2-step "enter support view?" dialog
with an optional reason field is a small follow-up; out of WU5.4 scope
because the API stores `null` happily and the audit row's existence is
the load-bearing signal, not its `reason` content.

After these notes were captured, re-ran the six-class checklist + the
full test sweep. No further material findings.

## Verification gate

From the worktree:

```bash
uv run ruff check .                 # All checks passed!
uv run pyright                      # 0 errors, 26 warnings (pre-existing stubs)
uv run pytest -m "not integration" -q
                                    # 340 passed, 4 skipped (pre-existing)
uv run pre-commit run --all-files   # every hook Passed

cd packages/horizons-webapp
npm run lint:check                  # oxlint + eslint clean
npm run test:unit -- --run          # 168 passed across 26 files
                                    # (134 baseline + 34 new)
npm run build                       # vue-tsc + Vite, 0 TS errors
```

The 34 new vitest cases break down as:

| Spec | Tests | Pins |
| --- | --- | --- |
| `api/__tests__/client-impersonation.spec.ts` | 2 | class 6 |
| `stores/__tests__/auth-impersonation.spec.ts` | 5 | classes 4, 5 + state transitions |
| `components/admin/__tests__/SupportViewBanner.spec.ts` | 4 | class 3 |
| `components/admin/__tests__/ScopeRemovalConfirmDialog.spec.ts` | 4 | class 2 |
| `views/__tests__/AdminClientsView.spec.ts` | 4 | clients list, pagination |
| `views/__tests__/AdminClientDetailView.spec.ts` | 6 | scope editor, class 5 |
| `views/__tests__/AdminAuditView.spec.ts` | 3 | audit table, filters, mode distinction |
| `router/__tests__/admin-guard.spec.ts` | 5 | route guard incl. mid-impersonation refusal |
| `__tests__/admin-integration.spec.ts` | 1 | end-to-end full flow |

Build is clean apart from the pre-existing `@vueuse/core`
`INVALID_ANNOTATION` warnings noted in `260605-wu50-vue-shell-and-auth-store.md`.

## Manual verification (for the user)

In-session tests cover the unit + integration surface. The end-to-end
"does it actually work against the real API" check:

1. Apply migrations + start the API: `uv run alembic upgrade head &&
   uv run uvicorn horizons_api.app:app --reload --port 8000`.
2. Seed an admin + at least one client. The simplest path is to copy
   the integration-test seeding pattern from
   `tests/api/test_admin_clients_endpoint.py`; a first-class
   `scripts/create_user.py` is WU8.1 work.
3. Start the webapp: `cd packages/horizons-webapp && npm run dev`.
4. Log in as the admin. **Expected**: post-login lands on
   `/admin/clients`, not `/`.
5. **Class 1 walk** — Open DevTools → Network. Refresh `/admin/clients`.
   **Expected**: one `GET /v1/admin/clients` call. Server-side audit
   row gets written (verify in `/admin/audit` after a moment).
6. **Class 2 walk** — Click a client to open the detail view. Click
   "Remove" on a scope. **Expected**: a modal opens listing the affected
   discovery documents. Close it with the X / Cancel / Esc / outside
   click. **Expected**: no `PATCH` fires in the Network tab. Re-open
   and click "Remove scopes". **Expected**: PATCH fires with
   `remove_scopes`, success toast reads
   "Scope removed — N watchlists soft-hidden".
7. **Class 3 walk** — Click "Enter support view". **Expected**: amber
   banner appears at the top of the page; tab title becomes
   `[SUPPORT] Horizons`; screen reader (if enabled) announces the
   support-view text. Navigate to `/changes`, `/watchlists`, `/`:
   **Expected**: banner persists across all of them.
8. **Class 4 walk** — While impersonating, F5. **Expected**: the page
   reloads, the cold-bootstrap refresh succeeds, the SPA re-enters as
   the original admin (no banner, no `[SUPPORT]` prefix, lands on
   `/admin/clients`).
9. **Class 5 walk** — Stop the API mid-flight, click "Enter support
   view". **Expected**: error toast appears, the SPA stays on
   `/admin/clients/:id`, no banner, no `[SUPPORT]` prefix.
10. **Class 6 walk** — In DevTools, monkey-patch the impersonation
    bearer's TTL to expire (or, simpler, wait 15 minutes). Trigger a
    `/v1/me` call by navigating. **Expected**: a 401 fires from
    `/v1/me`; no `POST /v1/auth/refresh` is sent; the SPA exits
    support view and shows "Support view expired" toast; bounces to
    `/admin/clients`; the admin is once again the original admin (their
    access token from before entry is back in `auth.accessToken`).
11. Click "Exit support view" in the banner. **Expected**: banner
    disappears, tab title returns to `Horizons`, SPA navigates to
    `/admin/clients`.
12. Open `/admin/audit`. **Expected**: rows for the admin's reads of
    `/admin/clients`, `/admin/audit`, and the impersonation event are
    visible. Filter `action=impersonation`: **Expected**: only the
    impersonation row remains.

## Follow-up wire-up

The WU5.4 step of the demo runbook (`docs/runbooks/demo.md`) has a
placeholder for the "Admin view + support view" section. Replace that
placeholder with the following — verbatim, no rewording:

````markdown
### Step 5 — Admin view + support view

1. Open a private window. Visit the SPA URL.
2. Log in with the admin credentials from
   `docs/runbooks/demo-accounts.md`. **Expected**: lands on
   `/admin/clients`, not `/`.
3. Walk the audience through the clients table. Mention the
   `Page 1 of N` indicator and the per-row Open button.
4. Open the UK demo client's detail page. Highlight the active
   subscription's scopes.
5. Add a new scope (e.g. `FR` + `banking`). The new row appears in
   the scopes table; the toast reads "Scope added".
6. Remove that scope. **Expected**: a confirmation modal listing
   matching documents from the discovery feed. Click Cancel — the
   modal closes and no API call fires.
7. Re-open Remove and confirm. **Expected**: success toast reads
   "Scope removed" (and "— N watchlists soft-hidden" if any).
8. Click "Enter support view". **Expected**: amber banner appears
   at the top of every page; tab title shows `[SUPPORT] Horizons`.
9. Navigate to `/changes` and `/watchlists` to show the banner
   persists across routes. Mention that the SPA is now rendering
   the **client's** view, with the client's scopes — same code,
   different bearer.
10. Click "Exit support view" in the banner. **Expected**: banner
    disappears, tab title returns to `Horizons`, lands on
    `/admin/clients`.
11. Open `/admin/audit` and filter `action=impersonation`. **Expected**:
    a row recording the impersonation event, with the admin's id,
    the target client's id, and the timestamp.

Recovery: if the SPA gets stuck in support view (e.g. a network blip
hid an exit toast), reload the page. The cookie-driven cold bootstrap
re-enters as the admin; the in-memory impersonation token is gone.
````

The placeholder comment in the demo runbook is on the line that says
something like `<!-- WU5.4 -->` or `TODO: support view`. A post-merge
agent (or the user) applies this verbatim — no editorial liberties.

## Cadence note

Worktree `worktree-wu5.4-admin-views-support-view` (relayed via
`EnterWorktree`). Local sweep → second-review pass → full sweep →
README update → journal → `/done` → fast-forward merge into `main` per
CLAUDE.md's CI / merge cadence. Direct push, no PR. Branch deletion
after merge is the EnterWorktree exit step.
