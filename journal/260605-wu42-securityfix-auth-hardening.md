# 2026-06-05 — WU4.2 security hardening (three fixes)

*Last revised: 2026-06-05.*
*Path: journal/260605-wu42-securityfix-auth-hardening.md.*

Push- and commit-time security review flagged three real
vulnerabilities in the auth-flow routes that landed with WU4.2. None
were exploitable as of the demo configuration today (there are no
production credentials, no SPA yet, no exposed surface) but all three
must be closed before WU5.0 wires the SPA against this API.

## What shipped

### 1. Cookie-source binding on refresh / logout (MEDIUM)

The original implementation derived the response shape on
`/v1/auth/refresh` and `/v1/auth/logout` from the client-controlled
`X-Client-Type` header. That gave XSS-controlled JS on the SPA's
origin a way around `HttpOnly`:

```js
// Attacker-controlled JS in the SPA. The browser attaches the
// refresh_token cookie automatically; X-Client-Type is omitted.
const { refresh_token } = await fetch('/v1/auth/refresh', { method: 'POST' }).then(r => r.json());
// refresh_token is now in JS heap — HttpOnly bypassed.
```

Fix: bind the response shape to the *token source* recorded by the
auth dep, not to any client-controlled header.

- `horizons_api.deps.refresh.RefreshTokenSource` — new `StrEnum`
  (`COOKIE` / `HEADER`).
- `_extract_refresh_token` now returns `(token, source) | None`.
- `require_refresh_principal` now returns `(Principal,
  RefreshTokenSource)`. `session_for_refresh` unpacks and ignores the
  source (it only needs the principal).
- `routes.auth.refresh` and `routes.auth.logout` no longer take an
  `X-Client-Type` parameter at all. The browser shape is selected
  when `source is RefreshTokenSource.COOKIE`.
- `routes.auth.login` continues to consult `X-Client-Type` because
  login has no prior context (no cookie, no header) to derive
  intent from — an explicit opt-in is the only available signal.

### 2. Account-enumeration via response-timing (MEDIUM)

`POST /v1/auth/login` previously short-circuited on `user is None`
without running argon2. Argon2 is ~50–200 ms by design; the unknown-
email branch completed in <5 ms, so probing emails and measuring
response time would have leaked which accounts exist.

Fix: precompute a sentinel argon2 hash at module import
(`_TIMING_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))`) and
always call `verify_password(plaintext=body.password,
password_hash=_TIMING_DUMMY_HASH)` on the missing-user branch. The
result is discarded; both branches now consume the same CPU budget.

### 3. Stale-role privileges on refresh (MEDIUM)

The original `refresh` handler used `principal.role` from the verified
JWT claim as the role of the new access token. That means a role
demotion (`admin` → `client`) or an account deletion in the database
wouldn't take effect until the refresh token's 30-day TTL expired.
Anyone holding a pre-demotion refresh could rotate freely and keep
admin access.

Fix: re-read the user row in the refresh handler via
`UsersRepository(session).get_by_id(principal.user_id)` and use
`user.role.value` for the new tokens. If the user row is gone (account
deletion), return 401. This makes the refresh boundary the
synchronisation point where role changes take effect, matching
`/v1/me`'s behaviour.

### Documentation

`docs/api/auth.md` now documents the cookie-source-binding posture
explicitly. The "Client-type signal" section calls out that
`X-Client-Type` is consulted on **login only** and explains the XSS
threat model that motivated the change. The login / refresh / logout
sections inherit the same shape rules.

### Tests (+5 regression)

In `tests/test_auth_endpoints.py`:

- `test_refresh_via_cookie_never_echoes_token_even_without_x_client_type`
  — logs in as browser, then `POST /v1/auth/refresh` *without* the
  `X-Client-Type: browser` header. The body must omit `refresh_token`
  and the `Set-Cookie` must be issued. This is the direct regression
  for finding 1.
- `test_logout_via_cookie_clears_cookie_even_without_x_client_type` —
  symmetric: cookie-sourced logout still emits the clearing
  `Set-Cookie` regardless of `X-Client-Type`.
- `test_login_missing_user_runs_argon2_for_timing_parity` — measures
  wall-clock time of the unknown-email branch and the wrong-password
  branch. Asserts both are ≥10 ms (proving argon2 ran) and that
  neither blows the ratio out by more than 5× (very generous; the
  real signal we want to fail is the "miss took <5 ms" case).
- `test_refresh_picks_up_role_change_at_rotation_boundary` — seeds an
  admin, logs in, demotes the user to client in the DB, then
  refreshes. The new access token's `role` claim must read `client`.
- `test_refresh_returns_401_if_user_row_disappeared` — deletes the
  user between login and refresh; refresh must 401 even though the
  refresh JWT signature is valid.

## Status by suite

- 413 default-marker tests passing (was 408 → +5 security
  regressions).
- ruff check / ruff format: clean.
- pyright strict: 0 errors.
- pre-commit all-files: clean.
- Webapp gate (`lint:check` / `build` / `vitest --run`): clean.

## Design notes worth keeping

1. **Cookie-source > X-Client-Type for refresh / logout, but login is
   the exception.** Login has no cookie yet, no JWT yet, no prior
   server-side state to lean on. An attacker who can `POST
   /v1/auth/login` with a victim's credentials has already won; the
   shape choice cannot be re-derived from anything else. So login
   keeps the explicit header opt-in. Refresh and logout, by contrast,
   *do* have an authenticated source (the verified token); that
   source is what gets bound.
2. **Header wins over cookie when both present.** The precedence is
   explicit so the security contract is unambiguous: the source the
   route gets is the one that *actually carried* the token through
   verification. A programmatic caller that happens to share a cookie
   jar can still drive the flow with explicit headers.
3. **The argon2 sentinel hash is minted at module import**, not per
   request. A per-request hash would defeat the timing parity (the
   `hash_password` step takes ~100 ms by itself); a hash minted at
   import is essentially free to verify against and adds zero
   per-request cost beyond the argon2 verify the wrong-password
   branch already pays.
4. **Refresh role re-read is a write-after-revoke read.** The order
   in the handler is: revoke the old jti first, then re-read the
   user. If the revoke succeeds (the token was live) but the user is
   gone by the time we read, we 401 — the access token is already
   dead because the rotation never finished, and the next refresh
   attempt with the old token returns 401 too (already revoked).
5. **No `X-Client-Type` parameter on refresh / logout signatures.**
   Removing it from the function signature is the point — leaving it
   in place "just in case" would have been a foot-gun: future
   contributors might wire it back into the shape logic. Removing
   it makes the misuse structurally impossible.

## Cadence note

Worktree `wu4.2-secfix-cookie-source-binding`. Merges into `main` via
fast-forward per `CLAUDE.md`'s CI / merge cadence.
