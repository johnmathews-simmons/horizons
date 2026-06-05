# Horizons API — Authentication

> Note: the other files in `docs/api/` document the upstream **Lawstronaut**
> API (the source we ingest from). This file documents the **Horizons** API
> our customers and the SPA call. They are unrelated surfaces.

The Horizons API uses self-rolled RS256 JWTs over a `TokenProvider` seam.
The same three flows — login, refresh, logout — serve two client postures:

- **Programmatic clients** (server-side integrations, CI scripts, the
  ingestion worker if it ever calls the public API). They manage their own
  bearer storage. The login response carries both tokens as JSON; subsequent
  requests use `Authorization: Bearer <access_token>`; refresh / logout
  present the refresh token in `Authorization: Bearer <refresh_token>`.

- **Browser clients** (the SPA at `webapp/`). The access token is held in
  memory only (JS heap, never `localStorage`); the refresh token is held in
  a `HttpOnly; Secure; SameSite=Lax` cookie the browser cannot read. Refresh
  / logout do not send the refresh token explicitly — it rides on the cookie.

One endpoint per flow serves both postures. The server's signal differs
by flow:

- **Login** uses the explicit `X-Client-Type: browser` request header.
- **Refresh / logout** use the *source* of the refresh token (cookie or
  `Authorization` header). `X-Client-Type` is **ignored** on these
  endpoints.

## Client-type signal: `X-Client-Type: browser` (login only)

Browser clients send `X-Client-Type: browser` on the login call. Anything
else — header absent, header value other than `browser` — is treated as
programmatic.

Why a custom header instead of `Accept` negotiation: `Accept` is overloaded
by intermediaries (proxies, CDNs may rewrite it) and the browser flow needs
a side-effect (`Set-Cookie`) the response body does not encode. The header
makes the choice explicit at the call site and survives any reasonable
proxy.

### Why refresh / logout do NOT consult `X-Client-Type`

On `/v1/auth/refresh` and `/v1/auth/logout` the response shape is bound
to the *source* of the refresh token. Cookie → browser-shaped response
(no refresh in body, `Set-Cookie` on rotation, clearing cookie on
logout); header → programmatic-shaped response (refresh in body, no
cookie touched).

This is a defence against an XSS-driven response-shape downgrade. If the
shape were chosen by `X-Client-Type` here, malicious JS on the SPA's
origin could call `fetch('/v1/auth/refresh')` — the browser attaches the
`HttpOnly` cookie automatically — and *omit* the header to coerce the
server into returning the rotated refresh token in JSON, where JS can
read it. `HttpOnly` would be effectively bypassed. Binding to the token
source closes the channel.

## `POST /v1/auth/login`

Request body (JSON):

```json
{ "email": "user@example.com", "password": "..." }
```

Outcomes:

- **400** — body missing / malformed (handled by FastAPI body validation).
- **401** — unknown email **or** wrong password. The two cases share a body
  to avoid leaking which accounts exist.
- **200** — credentials valid.

### Response shape — programmatic client

```json
{ "access_token": "eyJ...", "refresh_token": "eyJ..." }
```

No `Set-Cookie`. The client is responsible for safekeeping the refresh
token.

### Response shape — browser client (`X-Client-Type: browser`)

```json
{ "access_token": "eyJ..." }
```

Plus:

```
Set-Cookie: refresh_token=eyJ...; HttpOnly; Secure; SameSite=Lax;
  Path=/v1/auth; Max-Age=2592000
```

The cookie is scoped to `Path=/v1/auth` so it is only sent on auth-flow
calls (refresh, logout) and never on `/v1/me` or any data endpoint — those
use the in-memory access token via `Authorization`. `Max-Age` matches the
refresh-token TTL (default 30 days). The refresh token is **not** echoed
in the JSON body for browser clients.

## `POST /v1/auth/refresh`

Exchanges a refresh token for a new access token. The old refresh token is
rotated: a new one is issued and the old `jti` is marked revoked.

Token source:

- **Browser** — cookie `refresh_token`. No request body / Authorization
  header expected.
- **Programmatic** — `Authorization: Bearer <refresh_token>`. No cookie.

Outcomes:

- **401** — missing token; invalid signature; expired token; wrong-kind
  token (e.g. an access token presented to refresh); token's `jti` is
  already revoked or absent from `refresh_tokens`. Uniform body so the
  client cannot probe which branch fired.
- **200** — success.

### Response shape — programmatic

```json
{ "access_token": "...", "refresh_token": "..." }
```

### Response shape — browser

```json
{ "access_token": "..." }
```

Plus a new `Set-Cookie: refresh_token=...; HttpOnly; ...` with the rotated
refresh token and a fresh `Max-Age`.

## `POST /v1/auth/logout`

Revokes the active refresh token. Subsequent refresh attempts with the same
`jti` will 401.

Token source: same as refresh.

Outcomes:

- **401** — missing or invalid token (same rules as refresh).
- **204** — success. Body is empty. For browser clients the response
  carries `Set-Cookie: refresh_token=; HttpOnly; ...; Max-Age=0` to clear
  the cookie immediately.

Note: revoking the refresh token does **not** revoke the access token
currently in the client's memory — access tokens are 15-minute bearers and
are not individually tracked server-side. The expectation is that the
client (browser SPA or programmatic script) discards the access token at
logout and falls back to login next time. Any window between logout and the
access token's natural expiry is unavoidable without a per-request DB hit,
which the hot-path design (`verify_token` is pure crypto) excludes.

## `Cache-Control` posture on per-user responses

Every per-user response — `/v1/me`, `/v1/me/watchlists`, etc — carries:

```
Cache-Control: private, no-store
```

Auth-flow responses (`/v1/auth/login`, `/v1/auth/refresh`, `/v1/auth/logout`)
carry the same header for the same reason: the body contains tokens that
must not be cached by any intermediary or the browser.

## Impersonation tokens (admin support view)

A third token kind — `impersonation` — exists alongside `access` and
`refresh`. It is minted only by `POST /v1/admin/impersonate` (admin-only,
audited) and carries the impersonated client's `user_id` in `sub` and
`role='client'`. Its TTL is 15 minutes, the same as access tokens.

The mint endpoint writes one `admin_access_log` row with
`mode='impersonation'` and `target_user_id=<client>` **before** the
token is returned. That row is the single durable record of the
elevation event; a network blip after the mint cannot leave a working
token with no audit trail. The mint endpoint also writes one
`mode='operator'` row (from the standard admin dep) recording the URL
hit; both rows are intentional and are pinned by tests.

Token-kind acceptance matrix on client-facing routes:

| Endpoint class                | `access` | `impersonation` | `refresh` |
| ----------------------------- | -------- | --------------- | --------- |
| `/v1/me`, `/v1/me/*`          | ✓        | ✓               | 401       |
| `/v1/changes/*`               | ✓        | ✓               | 401       |
| `/v1/auth/refresh`            | 401      | 401             | ✓         |
| `/v1/auth/logout`             | 401      | 401             | ✓         |
| `/v1/admin/*`                 | ✓ (admin role) | 403 (client role) | 401 |

The `authenticated_user` dep accepts `{access, impersonation}` because
both represent "a human user acting on their own behalf"; the role
gate on `/v1/admin/*` (`require_admin_principal`) refuses
impersonation tokens by virtue of their `role='client'` claim. No
per-request audit row is written under impersonation: the entry row
plus the 15-minute TTL are the audit story. The webapp's support-view
banner is the live-deception mitigation.

Exit from support view is client-side: the SPA drops the
impersonation token from in-memory state. There is no
`/v1/admin/impersonate/exit` endpoint — a separate exit audit row
would add no durable signal beyond what the entry row already
records.

## Refresh-token registry

Issued refresh tokens are persisted to the `refresh_tokens` table at
issuance and revoked there at logout / rotation. The hot-path verifier
(`LocalJwtProvider.verify_token`) does **not** consult the table — only the
`/v1/auth/refresh` and `/v1/auth/logout` endpoints do. See WU4.0's journal
for the rationale.

A refresh-token row carries `jti`, `user_id`, `issued_at`, `expires_at`,
`revoked_at`. RLS keys reads / writes on `app.user_id` so a client can only
see / revoke its own rows.

## Client-type enforcement on logout / refresh response shape

The `X-Client-Type: browser` header must match the original login call's
posture: a token issued from a browser-flow login must be refreshed /
logged out with `X-Client-Type: browser`. Mismatched posture is not a
security boundary — both shapes round-trip the same JWT — but it is a
contract mismatch the SPA should not produce in practice. The server does
not reject mismatched posture; the response is shaped per the *current*
call's header.
