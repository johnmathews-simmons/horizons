# 2026-06-05 — WU4.0: Auth seam (TokenProvider + LocalJwtProvider)

Opens Track 4. The HTTP surface is not landed yet (that's WU4.1 in the
same session), but the seam between the API and whatever issues /
verifies tokens lives in `horizons-core` so it's testable in isolation
and stays portable when the EntraIdProvider lands post-demo.

## What shipped

### Auth surface (`horizons_core.core.auth`)

- `provider.py` — the `TokenProvider` `Protocol`, the `Principal`
  dataclass (verified subject of a JWT — `user_id`, `role`, `kind`,
  `jti`, `issued_at`, `expires_at`), the `TokenKind` `StrEnum`
  (`access` | `refresh` | `impersonation`), and two exceptions
  (`AuthError`, `InvalidTokenError`). The Protocol's three methods
  are `issue_token` (async, takes a `session` for refresh-kind
  writes), `verify_token` (sync, pure crypto), and `revoke_token`
  (async). Documented why all three are async-shaped (uniform across
  implementations; `LocalJwtProvider` writes a row on refresh
  issuance, EntraIdProvider will round-trip MSAL) and why
  `verify_token` deliberately does **not** consult the database
  (hot-path; refresh revocation is checked separately).

- `local_jwt.py` — `LocalJwtProvider`, RS256-pinned over PyJWT.
  Construction-time keys (PEM bytes), issuer, audience, per-`kind`
  TTLs, leeway (default 30s). Algorithm pinned via PyJWT's
  `algorithms=[...]` arg — this is what closes the historical
  `alg=none` and HS-with-RSA-public-key confusion classes. Constructor
  refuses `none` / `HS*` outright with a `ValueError`. `issue_token`
  for `REFRESH` persists the row via `RefreshTokensRepository.record`
  inside the caller's session bracket; the provider never owns
  session lifetime.

- `passwords.py` — thin `argon2.PasswordHasher` wrapper:
  `hash_password`, `verify_password`, `needs_rehash`. argon2id with
  the library's default parameters; `needs_rehash` lets the login
  path upgrade hashes lazily without a bulk migration.

### Database surface

- `migrations/versions/0008_refresh_tokens.py` — `refresh_tokens`
  table keyed on `jti` (the JWT id is the primary key — guaranteed
  unique by the issuer, no separate surrogate needed). Columns:
  `jti`, `user_id`, `issued_at`, `expires_at`, `revoked_at`. RLS
  enabled + FORCEd; three policies (`refresh_tokens_owner_select` /
  `_insert` / `_update`) all `TO api_app` keyed on `app.user_id`.
  Grants: `api_app` SELECT/INSERT/UPDATE (no DELETE — retired rows
  stay as audit), `admin_bypass` SELECT, no `ingestion_worker` grant.
  `ON DELETE CASCADE` from `users` so account-removal sweeps refresh
  tokens.

- `db/models/refresh_tokens.py` — `RefreshToken` ORM model mirroring
  the migration. Added to `db/models/__init__.py`'s re-export list.

- `repos/refresh_tokens.py` — `RefreshTokensRepository` with `record`,
  `get_by_jti`, `revoke`. `revoke` uses `RETURNING` rather than
  `CursorResult.rowcount` because the latter is not in the typed
  surface of SQLAlchemy 2.0's async `Result`; `.scalar_one_or_none()
  is not None` is the typed-clean signal of whether the UPDATE
  matched a row.

### Tests (+28 over WU3.3)

- `packages/horizons-core/tests/test_local_jwt.py` — 14 unit tests,
  no DB. Covers the four acceptance items:
  - **Forgery** — tamper the signature segment, verify rejects.
  - **Algorithm pinning** — `alg=none` rejected; `alg=HS256` rejected
    with an arbitrary HMAC secret. (The classical
    HS-with-RSA-public-key payload can no longer be *encoded* by
    PyJWT 2.x — its `prepare_key` for `HMACAlgorithm` raises on a
    PEM — so the test forges the JWS manually to exercise the
    verifier's pinning instead.)
  - **Expiry** — TTL of `-60s`, verify rejects.
  - **Clock skew** — `iat` 20s in the future accepted with 30s
    leeway; `iat` 5min in the future rejected. (`verify_iat=True` is
    explicit; PyJWT raises `ImmatureSignatureError`.)
  - **Plus**: round-trip success, wrong issuer, wrong audience,
    missing required claim (`role`), malformed `sub` UUID,
    constructor refusal of HS* / `none`, refresh-without-session
    misuse, access-with-session misuse.

- `packages/horizons-core/tests/test_passwords.py` — 4 unit tests:
  round-trip success, wrong-password rejection, per-call salt
  (identical plaintexts → different hashes), default params do not
  trigger `needs_rehash`.

- `tests/test_refresh_tokens_migration.py` — 6 integration tests
  (Postgres 18 testcontainer): columns + types, `schema_owner`
  ownership, index present, exact grant matrix, RLS enabled+FORCEd
  with the three policies, `ON DELETE CASCADE` from `users`.

- `tests/test_local_jwt_refresh_flow.py` — 4 integration tests:
  refresh issuance writes the row with matching `jti` and `expires_at`;
  `revoke_token` flips `revoked_at` and is idempotent (second revoke
  returns `False`); an attacker session cannot revoke another user's
  token (RLS + repo predicate both refuse; the owner's row stays
  live); revoking an unknown jti returns `False`.

### Doc updates

- `db/schema.md` — added a `refresh_tokens` section between
  `admin_access_log` and `watchlists` (shape, indexes, write
  semantics, isolation).
- `db/roles.md` — per-table grant row for `refresh_tokens`.
- `db/rls.md` — Status-by-table row + header bumped to
  `end of WU4.0`.
- `repos/repos.md` — `RefreshTokensRepository` added to the private-
  state aggregate table.

## Design decisions worth keeping

1. **Three token kinds at the seam, not at WU4.5.** The plan keeps
   `impersonation` as a third `TokenKind` from the start because the
   middleware decides what to do with each on every request. Pushing
   it in later would require touching `verify_token`'s callers; it's
   cheaper to bake it now and leave issuance unimplemented at the
   API layer until WU4.5.

2. **`verify_token` is sync and DB-free.** The middleware will run
   this on every authenticated request. A DB round-trip per request
   for revocation check is unnecessary — access tokens are 15-minute
   bearers and not individually revocable; refresh tokens are
   revocation-checked only at `/v1/auth/refresh` (WU4.2) where the
   client has just presented a refresh token by design. This shape
   keeps the hot path pure-crypto.

3. **`jti` is the PK on `refresh_tokens`.** A UUIDv4 jti is unique by
   construction at issuance; a separate surrogate `id` would buy
   nothing and would force every query to choose between the two
   columns. CASCADE on `users` deletion sweeps a user's tokens
   without needing a separate cleanup path.

4. **`revoke` returns a boolean rather than raising.** A cross-user
   revoke attempt is not a "this shouldn't happen" event — RLS will
   make it look exactly like "row doesn't exist", and the API layer
   already needs to map "not found" → 404 (not 403) to avoid leaking
   row existence. Returning `False` for both cases keeps the
   contract uniform.

5. **Refresh-token row write piggybacks on the caller's session.**
   The provider does not open / commit / close sessions. The
   caller's session bracket — the FastAPI `Depends` chain in WU4.1
   — is the lifetime owner. This matches the same posture as
   `core.auth.admin` (WU1.9) and the repository layer (WU1.6).

6. **`LocalJwtProvider.__init__` refuses HS* and `none` outright.**
   The Protocol allows any algorithm but the local provider is
   RSA-only by design. An HS-keyed configuration would change the
   verification-key distribution model (signing secret = verification
   secret) and likely indicate misconfiguration. Fail fast at
   construction so the seam doesn't silently weaken.

7. **HS-with-RSA-public-key confusion test forges the JWS manually.**
   PyJWT 2.x will not encode an HMAC token with PEM material as the
   secret (its `prepare_key` raises). The attack class still exists
   at the verifier layer, so the test constructs the b64url segments
   + HMAC signature by hand to demonstrate the verifier's pinning is
   the substantive defence. Logic captured in the test docstring so
   the next reader doesn't think the test is testing the wrong thing.

## Gotcha hit during implementation

`SQLAlchemy.Result[Any].rowcount` is not in the typed public surface
of `session.execute()`'s return value even though `CursorResult.rowcount`
exists at runtime — pyright (strict) flags the access. Switching to
`.returning(...).scalar_one_or_none() is not None` is cleaner than
`isinstance(result, CursorResult)` narrowing and avoids the imported
type, at the cost of one extra column round-trip on UPDATE (negligible
at demo scale).

## Status by suite (end of WU4.0)

- 361 default-marker tests passing (was 333 → +14 auth unit + 4
  password unit + 6 migration + 4 refresh-flow integration).
- ruff check / ruff format: clean.
- pyright strict: 0 errors (testcontainers `reportMissingTypeStubs`
  warnings unchanged — known third-party gap).
- pre-commit all-files: clean.

Webapp untouched by this unit; the gate runs anyway as a sanity check
before push.

## Next

WU4.1 (FastAPI app shell + auth middleware) sits on top of this seam
in the same session — it imports `TokenProvider`, the `authenticated_user`
dependency builds a `Principal` from the bearer, and the request scope
combines that with the WU1.5 `session_for_user` bracket to wire RLS
end-to-end.
