# 2026-06-06 — WU4.7: admin clients list + impersonation HTTP endpoints

*Last revised: 2026-06-06.*
*Path: journal/260606-wu47-admin-clients-and-impersonate.md.*

A small follow-up unit added between WU4.6 (OpenAPI regenerator) and
WU5.4 (admin webapp views). WU5.4 turned out to depend on two HTTP
surfaces the original plan deferred to "WU4.5 will add the admin
impersonation surface" without ever actually adding it — `GET
/v1/admin/clients` for the operator client list and `POST
/v1/admin/impersonate` to mint the audited impersonation token the SPA
holds during support view. Rather than improvise admin paths inside
WU5.4 (the prompt explicitly forbade that), the work was split out
into this unit so the impersonation surface gets its own
adversary-framed review pass before the webapp builds on top.

This is the second application of the named-adversary framing from
the [secfix-pattern retrospective](./260605-secfix-pattern-retrospective.md);
the first was the WU8.2 e2e hotfix (retroactively). Both Change 1
(named-adversary framing) and Change 2 (explicit second-review
constraint) applied here in full.

## What shipped

### `GET /v1/admin/clients`

New file `packages/horizons-api/src/horizons_api/admin/clients.py`.
Paginated list of `role='client'` users; admins excluded by
construction. Response envelope echoes the effective `limit` and the
total count so the SPA can render "page X of Y" without recomputing
the defaults. Ordering is stable on `(created_at ASC, id ASC)` so
offset paging behaves predictably across new signups.

Authorisation stack: `require_admin_principal` →
`admin_operator_session_for_request`. The dep is what writes the
audit row (one `operator`-mode row per request, `target_user_id NULL`)
**before** the route body runs — which is the defence against the
"admin enumerates client identifiers without leaving a trail"
adversary class.

`UsersRepository` gained `list_by_role(role, *, limit, offset)` and
`count_by_role(role)`. Both use plain SQLAlchemy `select()` — no
`sqlalchemy.text()` (the WU0.2 architectural test still passes).

### `POST /v1/admin/impersonate`

New file `packages/horizons-api/src/horizons_api/admin/impersonate.py`.
Only sanctioned mint path for `TokenKind.IMPERSONATION` bearers. The
flow, captured verbatim from the module docstring:

1. Resolve the admin (need their email for the response).
2. Resolve the target. A missing target is refused **before** the
   impersonation audit row is written.
3. Refuse self-impersonation (422) and admin-target impersonation
   (422). Policy refusals, not malformed input — the body is
   well-formed in both cases.
4. Enter `admin_impersonation_session` purely to commit the audit
   row (`mode='impersonation'`, `target_user_id=<target>`). The
   `_record_audit_row` semantics from WU1.9 mean the row commits
   in its own transaction before the working session yields, so
   the row survives any later failure in the route body.
5. Mint the impersonation JWT via the existing `TokenProvider`.
   `LocalJwtProvider` already supports `kind=IMPERSONATION` (no DB
   write needed; the audit row IS the durable record).
6. Return `{ impersonation_token, target_user_id, target_email,
   original_admin_id, original_admin_email, expires_in_seconds }`.
   The SPA banner has everything it needs in one round trip.

Exit is deliberately client-side. The SPA drops the impersonation
token from in-memory state and resumes its admin session. The
15-minute TTL bounds the elevation window; a separate `/exit`
endpoint would write a row that contains no information the entry
row doesn't already capture. Documented at the top of the module so
a future reader doesn't read the absence as an oversight.

### `authenticated_user` now accepts ACCESS + IMPERSONATION

`packages/horizons-api/src/horizons_api/deps/auth.py` refactored:

- New factory `require_kinds(*kinds)` accepts any of the given kinds.
- `require_kind(kind)` becomes a single-kind wrapper around
  `require_kinds`.
- `authenticated_user = require_kinds(TokenKind.ACCESS,
  TokenKind.IMPERSONATION)` — the dominant "human user acting on
  their own behalf" case.

This is a deliberate softening of the WU4.1 secfix property "kind is
checked at every authentication point". The new property is "every
authentication point checks the set of kinds it is prepared to
serve" — client-facing routes serve both kinds because IMPERSONATION
is the support-view bearer for the same set of operations; refresh
endpoints still serve only REFRESH; admin endpoints layer
`require_admin_principal` (role=admin) on top of `authenticated_user`
so impersonation tokens (role=client) are rejected at admin URLs by
the role gate, not the kind gate.

The existing `test_impersonation_token_rejected_at_me_endpoint`
regression test, originally added by the WU4.1 secfix, was renamed
to `test_impersonation_token_accepted_at_me_endpoint` and updated to
assert the kind gate now lets IMPERSONATION through. The test uses
`raise_server_exceptions=False` on the TestClient so the downstream
KeyError (no DB URL in the no-DB fixture) surfaces as a 500 response
rather than re-raising into the test — the only "auth dep rejected"
shape is `401 + {"detail": "invalid bearer token"}`, so any other
outcome means the kind gate accepted the token. The companion
negative test `test_refresh_token_rejected_at_me_endpoint` is
unchanged: REFRESH tokens still hit the auth-dep rejection.

## Five adversary classes & their defences

Per the secfix-pattern retrospective's named-adversary framing.

1. **Admin enumerating client identifiers without leaving an audit
   trail.** *Defence:* every `GET /v1/admin/clients` request runs
   through `admin_operator_session_for_request`, which writes one
   `admin_access_log` row (`mode='operator'`, `target_user_id NULL`)
   **before** yielding the session. *Pinned by:*
   `test_clients_list_writes_one_operator_audit_row_per_request` —
   delta of two new operator rows for two list calls, scoped to the
   acting admin.
2. **Wrong-target abuse on impersonate.** Three sub-adversaries:
   typo'd target (404 `target user not found` *before* any
   impersonation row is written), admin → admin impersonation (422
   `target is not a client`), admin → self impersonation (422
   `cannot impersonate yourself`). *Pinned by:*
   `test_impersonate_missing_target_returns_404`,
   `test_impersonate_admin_target_returns_422`,
   `test_impersonate_self_returns_422`. The 404 test additionally
   asserts zero impersonation-mode rows landed — a typo'd target
   doesn't leave an "impersonated NULL" row.
3. **Audit-row-missing-after-200.** Network failure between the
   audit-row write and the token mint could leave a working
   impersonation bearer with no durable elevation record. *Defence:*
   the route opens `admin_impersonation_session` PURELY to commit the
   audit row, then exits the with-block before minting the token.
   The audit row is committed by `_record_audit_row`'s own
   transaction *before* the impersonation session's working session
   yields (WU1.9 semantics); the mint follows. If the mint raises,
   the audit row is already on disk. *Pinned by:*
   `test_impersonate_writes_impersonation_audit_row_before_returning`
   asserts the row exists after success **and** asserts the
   companion operator-mode row from the dep also exists — pinning
   the deliberate 2-row pattern so a future refactor that drops
   either source can't quietly elide audit signal.
4. **Token-kind smuggling.** Variants: refresh bearer presented to
   the mint endpoint (rejected by the kind gate, uniform 401);
   minted impersonation token presented to an admin route
   (rejected by `require_admin_principal` — `role='client'`, not
   `admin`); impersonation token presented to refresh endpoints
   (rejected by `require_kind(REFRESH)`). *Pinned by:*
   `test_impersonation_token_cannot_reach_admin_endpoint` (403 at
   `/v1/admin/clients` despite valid impersonation bearer) and the
   pre-existing `test_refresh_token_rejected_at_me_endpoint`.
5. **Self-mint via missing auth.** No-bearer or non-admin caller
   should not be able to mint impersonation tokens. *Pinned by:*
   `test_impersonate_missing_bearer_returns_401`,
   `test_impersonate_non_admin_returns_403`.

## Deliberate 2-row audit pattern (and why)

A successful `POST /v1/admin/impersonate` writes **two**
`admin_access_log` rows:

- One `mode='operator'`, `target_user_id NULL` — from the
  `admin_operator_session_for_request` dep, recording "admin Y
  entered `/v1/admin/impersonate` at T".
- One `mode='impersonation'`,
  `target_user_id=<client>` — from the explicit
  `admin_impersonation_session` call, recording "admin Y began
  impersonating client X at T".

The split is intentional: the operator row also fires on routes that
refuse downstream (404 / 422), giving operators a complete log of
admin URL traffic regardless of outcome. The impersonation row is the
elevation event proper. Tests pin both rows so a refactor that
collapses the dep or the explicit context-manager call can't
silently lose audit signal. Documented at length in
`admin/impersonate.py`'s module docstring.

## Second-review pass

After the implementation was complete and tests green, ran a
deliberate second-pass adversarial review against each of the five
adversary classes:

- The audit-order test pinned the impersonation row but did NOT pin
  the operator row. A future refactor that dropped
  `admin_operator_session_for_request` from the route signature in
  favour of a bare `require_admin_principal` (because "the route
  body doesn't need the session, the mint just needs the provider")
  would silently halve the audit volume without failing any test.
  **Fixed:** extended `test_impersonate_writes_impersonation_audit_row_before_returning`
  to also assert at least one operator-mode row attributable to
  this admin. The route docstring was extended to explain the
  2-row pattern so a future reader understands the test's intent.

After the fix, re-ran the five-adversary checklist; no further
material findings.

## Security tradeoff worth recording

Per-request observability of impersonation traffic is intentionally
**not** added. When the SPA presents an impersonation token to
`/v1/me` (or any other client-facing route), the API sees
`principal.kind=IMPERSONATION` and `principal.user_id=<client>` —
RLS narrows visibility correctly — but the original admin's identity
is not propagated server-side and no per-request audit row is
written. The audit story is:

- One entry row at mint time (durable; persists for the audit
  window).
- 15-minute token TTL bounding the elevation window.
- The webapp's amber support-view banner as the operator-side
  live-deception mitigation (WU5.4).

This was the simplest correct design that lets one SPA bearer serve
the support-view flow. The alternative — adding `impersonator_id` to
`Principal` + the JWT claims + the `app.impersonating_admin_id` GUC
+ a per-request audit row — would propagate impersonation context
through the whole observability stack but is materially bigger work
and not needed for the 2026-06-08 demo. Captured here as a possible
post-demo enhancement.

## Decisions worth keeping

1. **No `/v1/admin/impersonate/exit` endpoint.** The 15-minute TTL
   is the bound; a server-side exit audit row contains no signal the
   entry row doesn't. Captured at the top of `admin/impersonate.py`
   so the absence isn't read as an oversight.
2. **Sibling `admin/` modules, not `routes/admin_*`.** The newer
   pattern (`admin/audit.py`, `admin/health.py`) is consistent —
   `admin/clients.py` and `admin/impersonate.py` slot in next to
   them. Legacy `routes/admin_subscriptions.py` is left in place;
   moving it would be churn unrelated to this WU.
3. **`require_kinds(*kinds)` factory + retargeted `authenticated_user`.**
   Tried two other shapes (adding a separate `client_facing_user`
   dep for impersonation-aware routes; adding `impersonator_id` to
   `Principal`) and rejected both. The first would have required
   touching every client-facing route's import + dep injection (~6
   files); the second would have rippled into the JWT claim shape +
   verify_token + observability. The factory + retarget is one file's
   worth of change with the same observable contract at every client
   route.
4. **TTL constant duplication.** The route's
   `_IMPERSONATION_TTL_SECONDS = 15 * 60` mirrors
   `LocalJwtProvider._DEFAULT_TTLS[TokenKind.IMPERSONATION]`. A
   future drift would silently desync the SPA's banner countdown
   from the actual token expiry. Noted as a post-demo cleanup
   target — one source of truth (e.g., a `TokenTTLs` constant
   surfaced by `core.auth`) is the right shape, but not in WU4.7's
   scope.
5. **Two audit rows per mint.** Intentional, pinned, and explained.
   See dedicated section above.

## Verification gate (from the worktree)

```bash
uv run ruff check .              # All checks passed
uv run pyright                   # 0 errors, 26 warnings (pre-existing)
uv run pytest                    # 564 passed, 4 skipped (-m nightly), 1 deselected
uv run pre-commit run --all-files
                                 # ruff-format: 2 files reformatted (auto)
                                 # regen-endpoints-md: stale → ran the regen,
                                 # second invocation: all hooks Passed.
cd packages/horizons-webapp
npm run lint:check               # oxlint + eslint clean
npm run test:unit -- --run       # 134 passed across 17 files
npm run build                    # 0 TS errors, vue-tsc + vite green
```

17 new integration tests (`test_admin_clients_endpoint.py`: 7;
`test_admin_impersonate_endpoint.py`: 10). 1 modified regression test
(`test_impersonation_token_accepted_at_me_endpoint`, formerly
`..._rejected_...`).

## Doc updates

- `docs/api/auth.md` — added "Impersonation tokens (admin support
  view)" section with a token-kind acceptance matrix (access /
  impersonation / refresh across `/v1/me`, `/v1/auth/*`,
  `/v1/admin/*`). Explains the 2-row audit pattern, the
  per-request-observability tradeoff, and the client-side exit
  decision.
- `docs/api/endpoints.md` — regenerated by pre-commit. Adds
  `GET /v1/admin/clients` and `POST /v1/admin/impersonate`.

## Follow-up wire-up

- **WU5.4 (admin views + support view) is now unblocked.** The
  webapp can hit `GET /v1/admin/clients` for the table view,
  `POST /v1/admin/impersonate` for the support-view entry, drop the
  impersonation token from memory on exit (no `/exit` endpoint
  needed), and use the response's `original_admin_email` to render
  the "return as ADMIN_EMAIL" affordance in the banner.
- **Post-demo cleanup**: one source of truth for the IMPERSONATION
  TTL (currently duplicated between `LocalJwtProvider` and
  `admin/impersonate.py`); optionally propagate `impersonator_id`
  through Principal / observability so per-request impersonation
  traffic is distinguishable in logs.

## Cadence note

Worktree `wu4.7-admin-clients-and-impersonate` (relayed via
`EnterWorktree`). Local sweep → second-review fix → full sweep →
docs → `/done`. Direct push to `main` per the CLAUDE.md cadence.
