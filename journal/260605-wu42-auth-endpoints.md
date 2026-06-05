# 2026-06-05 — WU4.2: `/v1/auth/{login,refresh,logout}`

Third Track-4 unit. Lays the HTTP surface for issuing, rotating, and
revoking the JWTs from WU4.0 with one endpoint per flow serving both
postures (programmatic JSON + browser cookie). Docs at
`docs/api/auth.md`.

## What shipped

### Routes (`horizons_api.routes.auth`)

- `POST /v1/auth/login` — `email + password` → `{access_token,
  refresh_token?}`. Verifies via `argon2.PasswordHasher`, binds
  `app.user_id` inside the same transaction once the user is found,
  then issues both tokens. Browser callers (`X-Client-Type: browser`)
  get the refresh in a `HttpOnly; Secure; SameSite=Lax; Path=/v1/auth`
  cookie and the JSON body omits it; programmatic callers get both in
  JSON.
- `POST /v1/auth/refresh` — accepts the refresh token from either the
  `refresh_token` cookie or `Authorization: Bearer`. Verifies the
  kind, runs `RefreshTokensRepository.revoke` to confirm liveness +
  revoke, then mints + persists a fresh pair. Replay of an already-
  revoked token returns `401` (uniform body, no branch leak).
- `POST /v1/auth/logout` — same token source; revokes the active
  refresh row, returns `204`; browser callers get a clearing
  `Set-Cookie: refresh_token=; Max-Age=0` on the same path.

All three responses carry `Cache-Control: private, no-store`.

### Dependencies (`horizons_api.deps`)

Two new entries layered on the WU4.1 set:

- `login_session_dep` (`deps/anon_session.py`) — a session bracket
  with role `api_app` but **no** `app.user_id` bound. Login uses this
  for the email-keyed user lookup, then the route calls
  `bind_app_user_id(session, user.id)` once the user is identified so
  the refresh-token insert satisfies the `refresh_tokens_owner_insert`
  `WITH CHECK` predicate. The "no GUC at entry" shape is documented in
  the dep module — callers outside login should not use it.
- `require_refresh_principal` + `session_for_refresh` (`deps/refresh.py`)
  — the refresh / logout counterparts of `authenticated_user` +
  `session_for_request`. The dep extracts the bearer from cookie OR
  header (header wins if both present), verifies signature + kind +
  claims, returns the `Principal`. Liveness against `refresh_tokens`
  lives in the route, not the dep, so the dep stays pure-crypto and
  the DB hit is per-request, not per-dep.

### Database / library helpers

- `horizons_core.db.session` gains `unauthenticated_session(engine)`
  and `bind_app_user_id(session, user_id)`. The text() interpolation
  carve-out is preserved (still single-file).
- `horizons_core.repos.users.UsersRepository` — read-only repo with
  `find_by_email` and `get_by_id`. No write surface today; WU4.5 will
  add admin-side writes.
- `horizons_core.core.auth` re-exports `verify_password` so the route
  imports the auth surface and not the internal module.

### Docs

- `docs/api/auth.md` — auth contract (the two postures, the
  `X-Client-Type` signal, the per-endpoint outcomes, the
  cookie semantics, the cache header).

### Tests (+12 integration, in `tests/test_auth_endpoints.py`)

All three flows, both postures, plus the negative cases:

| Test | Asserts |
| --- | --- |
| `test_login_programmatic_returns_both_tokens_and_writes_refresh_row` | 200 + JSON tokens + `refresh_tokens` row inserted; `Cache-Control: private, no-store`; no cookie |
| `test_login_browser_sets_httponly_cookie_and_omits_refresh_from_body` | 200; refresh in cookie with `HttpOnly`/`Secure`/`SameSite=Lax`/`Path=/v1/auth`; body has no `refresh_token` |
| `test_login_wrong_password_returns_401_uniform` | 401 with `"invalid credentials"` |
| `test_login_unknown_email_returns_401_uniform` | Same body as wrong password — account enumeration defence |
| `test_refresh_programmatic_rotates_and_revokes_old` | 200, old jti `revoked_at` set, new jti live |
| `test_refresh_browser_rotates_cookie` | 200, new `Set-Cookie` with rotated refresh |
| `test_refresh_replay_after_revoke_returns_401` | A second presentation of the same refresh token returns 401 |
| `test_refresh_rejects_access_kind_token` | An access token presented to refresh returns 401 (kind gate) |
| `test_refresh_missing_token_returns_401` | No cookie, no bearer → 401 |
| `test_logout_programmatic_revokes_jti` | 204, `revoked_at` set; `Cache-Control` preserved |
| `test_logout_browser_clears_cookie` | 204, `Set-Cookie: refresh_token=; Max-Age=0` issued |
| `test_logout_missing_token_returns_401` | 401 |

## Design decisions worth keeping

1. **`X-Client-Type: browser` header**, not Accept-negotiation. Cleaner
   than content-negotiation for a flow whose distinction is a
   side-effect (`Set-Cookie`), survives proxy normalisation, and the
   default (programmatic) means a misconfigured browser client fails
   safely rather than silently exposing the refresh in JSON.
2. **Cookie scoped to `Path=/v1/auth`**, not `/`. The refresh cookie
   has no reason to be attached to `/v1/me` or data endpoints — those
   use the in-memory access token via `Authorization`. Limits CSRF
   surface and means the cookie isn't replayed on every data request.
3. **Login's session has no `app.user_id` bound at entry.** Alternative
   designs would have either (a) used `admin_bypass` for the user
   lookup or (b) opened two sessions per login. Option (a) hands the
   login path a `BYPASSRLS` role unnecessarily; (b) double-transactions
   per login. The chosen shape — one session, role `api_app`, GUC
   bound mid-transaction via `bind_app_user_id` — keeps the bracket
   minimal and the role unchanged.
4. **Refresh-token liveness check sits in the route**, not in the dep.
   The dep verifies signature + kind + claims (pure crypto, no DB);
   the route does the `RefreshTokensRepository.revoke` round-trip and
   raises on missing/revoked. Symmetry with WU4.0's posture — the hot
   path stays DB-free, only the revocation-bearing endpoints touch
   the registry.
5. **`X-Client-Type` is the *current call's* posture**, not enforced
   against the original login's posture. A token issued via browser
   login refreshed via a programmatic call (or vice versa) is not a
   security boundary — both shapes round-trip the same JWT — so the
   server shapes the response by the current header. Documented in
   `docs/api/auth.md`.
6. **`revoke` returning `False` covers both "missing" and "already
   revoked".** Both cases return the same 401 to the client. The
   route does not need to distinguish; doing so would leak whether a
   given jti ever existed.

## Gotchas hit during implementation

- **`AsyncSession` and `TokenProvider` must be imported at runtime in
  route files**, not under `TYPE_CHECKING`. Same WU4.1 trap revisited:
  FastAPI resolves `Annotated[T, Depends(...)]` via `get_type_hints`
  at app-construction time and falls back to treating the parameter
  as a query string if the type isn't importable. Pyproject already
  has the per-file ignores for `routes/*.py` from WU4.1; this WU added
  the runtime imports.
- **`fastapi.testclient.TestClient` defaults to `http://testserver`.**
  The browser-flow cookie is `Secure`, so it would never be sent back
  on a plain-http request — refresh / logout tests under the cookie
  posture would silently 401. Fix: instantiate `TestClient(app,
  base_url="https://testserver")` so the secure cookie is included on
  subsequent requests.
- **`pydantic.EmailStr` needs `pydantic[email]`** (i.e.
  `email-validator`). Added to `packages/horizons-api/pyproject.toml`.
- **pyright strict + httpx**: `TestClient.get(...)` returns
  `Unknown` in strict mode (httpx ships partial stubs). The existing
  `packages/horizons-api/tests/test_app_auth.py` sidesteps this by
  living outside the pyright `include` list. New tests in `tests/` add
  a file-level pragma `# pyright: reportUnknownMemberType=false,
  reportUnknownVariableType=false, reportUnknownArgumentType=false`,
  matching the same posture rather than moving the tests out of the
  cross-package suite.

## Status by suite (end of WU4.2)

- 400 default-marker tests passing (was 388 → +12 auth-flow
  integration tests).
- ruff check / ruff format: clean.
- pyright strict: 0 errors.
- pre-commit all-files: clean.
- Webapp gate (`lint:check` / `build` / `vitest --run`): clean.

## Track 4 status

| WU | Status |
| --- | --- |
| WU4.0 | shipped (`core/auth/{provider,local_jwt,passwords}.py` + `refresh_tokens` table) |
| WU4.1 | shipped (`api/app.py`, `deps/*`, `routes/health.py`, `routes/me.py` stub) |
| **WU4.2** | **shipped (`routes/auth.py`, `deps/{anon_session,refresh}.py`, `repos/users.py`, `docs/api/auth.md`)** |
| WU4.3 | next — `/v1/me` real implementation + watchlists CRUD with scope validation |
| WU4.4 | depends on WU3.4 + WU4.1 — `/v1/discovery`, `/v1/temporal`, `/v1/differential` |

## Cadence note

This unit and WU4.3 land in the same worktree
(`wu4.2-4.3-auth-endpoints-and-watchlists`) and the same merge to
`main` — see `260605-wu43-me-and-watchlists.md` for WU4.3 and the
final test/verify status.
