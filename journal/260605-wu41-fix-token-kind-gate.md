# 2026-06-05 — WU4.1 follow-up: enforce token kind at the auth boundary

*Last revised: 2026-06-05.*
*Path: journal/260605-wu41-fix-token-kind-gate.md.*

The push-time security review flagged a real defect in the WU4.1 auth
dependency. `authenticated_user` verified the JWT signature / issuer /
audience / expiry but never checked the `kind` claim, so a `REFRESH`
or `IMPERSONATION` token presented as a bearer to `/v1/me` was accepted.
The `provider.py` module docstring already promised the check
("`kind` is checked at every authentication point so a refresh token
cannot be presented as a bearer to `/v1/me`"), but the enforcement
was missing.

## What shipped

- `packages/horizons-api/src/horizons_api/deps/auth.py` rewritten
  around a `require_kind(kind: TokenKind)` factory. The closure is
  the FastAPI dependency — extracts the bearer, verifies signature /
  claims via `_verify_bearer`, then asserts `principal.kind is kind`
  before returning. Wrong-kind tokens raise the same uniform 401
  body as any other auth failure so the client cannot distinguish
  the branch.
- `authenticated_user = require_kind(TokenKind.ACCESS)` keeps the
  dominant case ergonomic. Refresh and impersonation routes (WU4.2,
  WU4.5) build their own dep via the same factory; the kind
  expectation now lives next to the route declaration, not buried
  in a single shared dep.
- `deps/__init__.py` re-exports `require_kind` alongside the existing
  three deps; the package docstring updated to list four deps
  layered.

### Tests (+2)

- `test_refresh_token_rejected_at_me_endpoint` — forges a JWT
  directly via `jwt.encode` with `kind=refresh` (a valid
  RS256-signed token against the configured keypair; the only thing
  wrong is the kind claim) and asserts `/v1/me` returns 401. The
  forge-via-`jwt.encode` shape sidesteps the DB round-trip
  `LocalJwtProvider.issue_token(kind=REFRESH)` would otherwise
  require, since the kind check sits upstream of any DB write.
- `test_impersonation_token_rejected_at_me_endpoint` — mints an
  IMPERSONATION token through `LocalJwtProvider` (no DB session
  needed for that kind) and asserts the same 401 plus the uniform
  body `{"detail": "invalid bearer token"}` so the response shape
  matches every other auth-failure branch.

## Status by suite

- 388 default-marker tests passing (was 386 → +2).
- ruff check / ruff format: clean.
- pyright strict: 0 errors.
- pre-commit all-files: clean.

## Why this slipped through the original WU4.1

The `provider.py` docstring described the intended invariant in
prose, but no test exercised the violation case — every `/v1/me`
test in the original WU4.1 used `kind=ACCESS`. The lesson is that
"docstring says X" is not a contract; the regression tests are. The
two new tests here close the gap and would have caught the absent
check on the first run.
