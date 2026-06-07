# 2026-06-05 — WU8.2 hotfix: five-bug e2e stability fix

*Last revised: 2026-06-05.*
*Path: journal/260605-wu82-hotfix-e2e-cors.md.*

Worktree `wu8.2-hotfix-e2e-stability`. Scoped hotfix on top of WU8.2:
`.github/workflows/e2e.yml` had been red on every push to `main` since
`173c0c1`. The follow-up `47379bf` ("idempotence + smoke-test hardening")
addressed neither the CORS issue nor the four bugs sitting behind it.

The "hotfix" framing in the original prompt assumed timing or wait-step
stability tightening. Local reproduction (`e2e/README.md`'s recipe,
verified during this session) revealed the real shape: **five
unconnected latent bugs**, only the topmost of which looked like a
30-second CI timeout. Each bug was masking the next.

## Root cause(s)

Five real bugs, surfaced in order as each fix unblocked the next:

### Bug 1 — API CORS allow-list omits `X-Client-Type`

`packages/horizons-api/src/horizons_api/app.py:67` (pre-fix):

```python
allow_headers=["Authorization", "Content-Type"],
```

The webapp's login axios call sends `X-Client-Type: browser`
(`packages/horizons-webapp/src/api/auth.ts:12`) — the opt-in for the
cookie-shaped auth response. The browser's CORS preflight lists this
header in `Access-Control-Request-Headers`; Starlette's CORSMiddleware
returns **400 "Disallowed CORS headers"**, the browser blocks the
`POST`, the UI shows "Sign-in failed", `waitForURL` times out at 30 s.

Evidence: `uvicorn.log` from failing run `27038309539` contains exactly
one auth-related request — `OPTIONS /v1/auth/login HTTP/1.1 400 Bad
Request`. No `POST` ever fires.

Why latent: local `npm run dev` proxies through Vite (same origin → no
preflight). CI is the first cross-origin run.

### Bug 2 — `HORIZONS_DB_URL` driver mismatch in workflow

`.github/workflows/e2e.yml` (pre-fix) declared a single job-level
`HORIZONS_DB_URL=postgresql+psycopg://...`. Alembic and `seed_e2e.py`
need the sync `+psycopg` driver; the runtime
(`packages/horizons-core/src/horizons_core/db/session.py:68-71`) calls
`create_async_engine(url, connect_args={"statement_cache_size": 0})`,
which is an **asyncpg-only** kwarg. With `+psycopg` at runtime, the
first authenticated request 500s with:

```
psycopg.ProgrammingError: invalid connection option "statement_cache_size"
```

Why latent: Bug 1 stopped any request from ever reaching the DB layer.

### Bug 3 — RFC-6761 `.test` TLD rejected by `EmailStr`

`LoginRequest.email: EmailStr` (`routes/auth.py:67`) delegates to
`email-validator`. RFC-6761 TLDs (`.test`, `.invalid`, `.localhost`,
`.local`, `.example`) are rejected as "special-use or reserved names".
The WU8.2 seed deliberately chose `uk-client@e2e.test` *because*
RFC-6761 reserves `.test` for testing — but pydantic's default
validation kicks the request out with **422**:

```
"value is not a valid email address: The part after the @-sign is a
 special-use or reserved name that cannot be used with email."
```

Why latent: Bugs 1 and 2 stopped the request body from ever being
validated.

### Bug 4 — Webapp router never refreshes on cold SPA bootstrap

`packages/horizons-webapp/src/router/index.ts:44-53` (pre-fix) had a
**synchronous** `beforeEach` guard. When the test does
`await page.goto('/changes')` (a full HTTP navigation, not a vue-router
transition), the SPA bootstraps fresh, the in-memory access token is
null, the guard sees `requiresAuth && !isAuthenticated`, redirects to
`/login` immediately. **`/v1/auth/refresh` is never called** — the
HttpOnly cookie sits there unused.

Why latent: `npm run dev` and SPA-internal clicks (which preserve the
in-memory token) never exercise the cold-bootstrap path. The e2e is the
first place a `page.goto` lands on an auth-gated route.

This is also a user-visible bug: any F5 / Cmd-R / pasted-URL on
`/changes` would boot the user out. Fixed by making the guard async
and trying `auth.refresh()` once per bootstrap before deciding to
redirect.

### Bug 5 — Webapp sends `Authorization` on `/v1/auth/{refresh,logout}`

`packages/horizons-webapp/src/api/client.ts:44-52` (pre-fix)
unconditionally adds `Authorization: Bearer <accessToken>` on every
request whenever the bridge has a token. The three auth endpoints
(login, refresh, logout) carry an existing `_skipAuthRefresh: true`
flag in axios config; until now nothing read it on the *request* side.

API's `_extract_refresh_token`
(`packages/horizons-api/src/horizons_api/deps/refresh.py:82-101`) has an
explicit precedence rule:

> Header wins when both are present so a programmatic caller that
> happens to share a cookie jar with the same browser session can still
> drive the refresh / logout flow explicitly.

So on `/v1/auth/logout`, the browser sends both `Authorization: Bearer
<access_token>` AND the refresh cookie; the API picks the header,
validates it (succeeds — it's a valid JWT), then `kind` checks → access,
not refresh → **401**.

This means *every* browser logout silently fails server-side: the cookie
never gets revoked, the `refresh_tokens` row never gets marked revoked,
only the client-side `accessToken` clears. The unit test for logout
(`stores/__tests__/auth.spec.ts:34`) mocks `/v1/auth/logout` to 204
regardless, so the contract violation never surfaced.

The 401-triggered refresh in `client.ts:67-82` is also broken by the
same precedence: when an access token expires (~15 min), the interceptor
calls refresh with the (still-attached) expired Authorization header,
API rejects on the kind check, user is silently force-logged-out.

Why latent: WU5.0 / WU5.1 unit tests use msw mocks that don't enforce
the API's precedence contract. The e2e is the first round-trip that
exercises the real auth flow against the real route.

Fixed by tying the request-interceptor's "should I add Authorization?"
decision to the existing `_skipAuthRefresh` flag, which is already on
exactly the three endpoints where the header is wrong:

```ts
if (token && !config._skipAuthRefresh) { ... }
```

## Scope conflict and three pause-for-input rounds

The hotfix prompt's touch-list explicitly excluded
`packages/horizons-api/src/`, `packages/horizons-core/`, and
`packages/horizons-webapp/src/`. Bugs 1, 4, and 5 all fall outside that
list. After each local-repro failure I paused, wrote a
`.engineering-team/runs/manual-260605-e2e-hotfix/bug-N-*.md` analysis
with code citations, and asked the user before widening. The user
widened scope each time on the basis that the e2e couldn't be made
green any other way.

The widened touch list, final:

- `packages/horizons-api/src/horizons_api/app.py` (Bug 1)
- `.github/workflows/e2e.yml` (Bug 2)
- `packages/horizons-api/scripts/seed_e2e.py` (Bug 3)
- `packages/horizons-webapp/e2e/login-and-scope.spec.ts` (Bug 3)
- `packages/horizons-webapp/e2e/README.md` (Bug 3, doc consistency)
- `packages/horizons-webapp/src/router/index.ts` (Bug 4)
- `packages/horizons-webapp/src/api/client.ts` (Bug 5)
- `tests/test_create_demo_accounts.py` (pre-existing ruff-format drift
  blocking the local pre-commit gate)
- `journal/260605-wu82-hotfix-e2e-cors.md` (this entry)

## Commits

Each bug is its own commit so attribution is unambiguous and a future
bisect can land on the exact change that fixed the symptom they're
seeing:

1. **chore(tests): apply pre-existing ruff-format drift fix.** One-line
   unwrap in `tests/test_create_demo_accounts.py`. Pre-existing on `main`
   independent of this hotfix; confirmed by stashing my changes and
   re-running `pre-commit run --all-files` against a clean main. Splits
   into its own commit so Session M's attribution stays intact.
2. **fix(api): allow X-Client-Type in CORS allow-list.** Bug 1.
3. **fix(ci): give the API runtime step its own +asyncpg DB URL.**
   Bug 2. Alembic / seed keep the job-level `+psycopg` URL.
4. **fix(e2e): rename fixture emails off RFC-6761 .test TLD.** Bug 3.
   `seed_e2e.py`, `login-and-scope.spec.ts`, `e2e/README.md`.
5. **fix(webapp): try refresh-from-cookie on cold SPA bootstrap.**
   Bug 4. `router/index.ts` only.
6. **fix(webapp): suppress Authorization on /v1/auth/{login,refresh,logout}.**
   Bug 5. `client.ts` only.
7. **docs(journal): record the WU8.2 hotfix.** This entry.

## Local reproduction

Used the recipe in `packages/horizons-webapp/e2e/README.md` with two
port deviations (host postgres on 5432, host vite dev servers on 5173 /
5174 — moved to 5433 and 6173 respectively). The CORS origin and
Playwright base URL track the webapp port.

```bash
docker run --rm -d --name horizons-e2e-pg \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=postgres -p 5433:5432 postgres:18-alpine

# Generate ephemeral RSA pair (mirrors the workflow step).
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/e2e-private.pem
openssl rsa -in /tmp/e2e-private.pem -pubout -out /tmp/e2e-public.pem

# Migrate + seed (sync driver).
HORIZONS_DB_URL='postgresql+psycopg://postgres:postgres@localhost:5433/postgres' \
  uv run alembic upgrade head
HORIZONS_DB_URL='postgresql+psycopg://postgres:postgres@localhost:5433/postgres' \
  uv run packages/horizons-api/scripts/seed_e2e.py

# API (async driver).
HORIZONS_DB_URL='postgresql+asyncpg://postgres:postgres@localhost:5433/postgres' \
HORIZONS_CORS_ORIGINS='http://localhost:6173' \
HORIZONS_JWT_ISSUER='horizons-e2e' \
HORIZONS_JWT_AUDIENCE='horizons-e2e' \
HORIZONS_JWT_PRIVATE_KEY_PEM="$(cat /tmp/e2e-private.pem)" \
HORIZONS_JWT_PUBLIC_KEY_PEM="$(cat /tmp/e2e-public.pem)" \
uv run uvicorn 'horizons_api.app:create_app' --factory --port 8000

# Webapp.
cd packages/horizons-webapp
npm run build
npx vite preview --port 6173

# Test.
HORIZONS_E2E_BASE_URL=http://localhost:6173 \
  ./node_modules/.bin/playwright test
```

Final run, all five fixes applied:

```
Running 1 test using 1 worker
  ✓  1 [chromium] › e2e/login-and-scope.spec.ts:39:1 › UK + EU clients see disjoint clause-diff views (1.7s)
  1 passed (2.3s)
```

## Verification gate (in worktree, post-fix)

```text
uv run ruff check .                       # All checks passed!
uv run pyright                            # 0 errors, 26 warnings (pre-existing stubs)
uv run pytest -m "not integration" -q     # 329 passed, 4 skipped, 215 deselected
uv run pre-commit run --all-files         # all hooks Passed

cd packages/horizons-webapp
npm run lint:check                        # oxlint + eslint clean (0 warnings)
npm run test:unit -- --run                # 17 files, 134 tests, all pass
npm run build                             # vue-tsc + rolldown built in 363ms

# Local e2e against the recipe above:
./node_modules/.bin/playwright test       # 1 passed (2.3s)
```

CI verification on the feature branch follows the push; the e2e workflow
runs against the actual Postgres-service-container shape.

## Stability budget

**Zero slack added to the test or its harness.** No `navigationTimeout`
increase, no `test.slow()`, no retry-count change (still
`retries: 0` from `47379bf`), no additional `waitForResponse` shims, no
extra `curl` health-checks in the workflow. The original WU8.2 timing
was already correct — the failures were never about timing.

If a future regression introduces a real race, the budget is available
to be spent then. Don't pre-allocate.

## Beyond-e2e impact of the webapp fixes

Bugs 4 and 5 are user-visible auth-flow bugs that the e2e happened to
surface, not e2e-specific quirks:

- **Bug 4:** Without the cold-bootstrap refresh, any user reloading the
  app on an auth-gated route gets booted to `/login`. After the fix, F5
  on `/changes` keeps the user authenticated.
- **Bug 5:** Without skipping `Authorization` on refresh/logout, browser
  logout silently fails server-side (cookie isn't revoked, only the
  client-side token clears), and any 401-triggered refresh after the
  access-token TTL silently force-logs-out the user.

Both are now fixed as a side-effect of getting the e2e green. The
journal flags this for the demo audience — they'll see a smoother auth
flow than the WU8.2 author tested against, which matters more on
2026-06-08 than the e2e itself.

## Things considered, then dropped

1. **Removing `X-Client-Type` from the webapp** to dodge Bug 1 entirely.
   Would technically pass the preflight, but the header is the contract
   that opts the response into the cookie-shaped path (`routes/auth.py:5-24`).
   Removing it would break the cookie-based refresh flow that WU5.0 secured.
2. **Same-origin workaround in the e2e workflow** (reverse-proxy the
   webapp behind uvicorn) to dodge Bugs 1 and 5. Possible but invasive;
   adds a Caddy / nginx step for CI only, masks the underlying bugs from
   any local-dev cross-origin run, and would not have surfaced Bugs 2,
   3, 4 or 5 as separate items.
3. **API-side fix for Bug 5** (smarter precedence in `_extract_refresh_token`):
   accept the header but fall back to cookie on a kind mismatch. Cleaner
   in a way but changes how every refresh/logout request resolves auth
   precedence, including programmatic clients we haven't built yet. The
   webapp fix is smaller and contract-faithful.
4. **Bumping the e2e per-test timeout** as the original prompt's option B
   suggested. None of the failures were timing-related; bumping the
   timeout would only delay each red CI run.
5. **Adding new tests or assertions.** Explicitly forbidden by the
   prompt; declined throughout. Bug 5 in particular would benefit from a
   regression test in `client.spec.ts` asserting Authorization is omitted
   for `_skipAuthRefresh: true` requests — left for a follow-up.

## Session boundary note

The widened scope touches files outside this WU's nominal lane:

- `packages/horizons-api/src/horizons_api/app.py` — Session L's lane
  ordinarily. The change sits in the create-app factory's CORS block
  (lines 61-68), well away from the router-mount block where L is
  active. Conflict risk: low.
- `packages/horizons-webapp/src/router/index.ts` and
  `packages/horizons-webapp/src/api/client.ts` — Session O's lane. O is
  working on runtime config + watchlists; both files were last touched
  by WU5.0 / WU5.1 and aren't on O's current path. Conflict risk: low.
- `tests/test_create_demo_accounts.py` — Session M most recently. The
  change is a one-line ruff-format unwrap, no functional effect.

Each is one of the bugs the e2e exposed; none is a refactor.

## Cadence note

Worktree `wu8.2-hotfix-e2e-stability`. Fast-forward merge into `main`
per `CLAUDE.md`'s CI / merge cadence after the feature-branch CI run is
green.

## Post-merge follow-ups

1. **Regression tests for Bugs 4 and 5.** Both unit-testable in
   `packages/horizons-webapp/src/router/__tests__/` and
   `packages/horizons-webapp/src/api/__tests__/` (or `stores/__tests__/`).
   Out of this hotfix's scope; the e2e is currently the only thing that
   would catch either regressing.
2. **A short note in `docs/runbooks/demo-accounts.md`** that
   `@e2e.example.com` is the e2e flavor, distinct from Session M's
   `@e2e.test` demo accounts. Did not edit this doc in this hotfix
   (out of scope), but cross-reference is worth adding.
3. **Consider `_skipAuthRefresh` naming.** The flag now signals two
   things: "don't retry-on-401" and "don't send Authorization". Renaming
   to `_authEndpoint: true` or splitting into two flags would be
   clearer. Cosmetic; not urgent.
