# 2026-06-05 — WU4.3: `/v1/me` (real) + watchlists CRUD + scope trigger

Fourth Track-4 unit. Closes Track 4's "private state" surface and
makes the watchlists table do real work — each row is now a (user,
document) pair and the database backs the service-layer scope check
with a `BEFORE INSERT OR UPDATE OF document_id` trigger.

## What shipped

### Schema evolution (`migration 0009`)

- `watchlists.document_id uuid NOT NULL REFERENCES documents(id) ON
  DELETE CASCADE` — a watchlist is now a watched document. Indexed
  (`idx_watchlists_document_id`) for the future "who watches X?"
  reverse-lookup; `UNIQUE (user_id, document_id)` so a user cannot
  watch the same document twice.
- `app_private.assert_watchlist_in_scope()` — `BEFORE INSERT OR UPDATE
  OF document_id ON watchlists`. SECURITY DEFINER, empty `search_path`.
  Reads `app.user_id`; **short-circuits silently when unset** so the
  migration tests / superuser admin paths keep working. Under `api_app`
  with the GUC bound, joins through to `app_private.current_scope()`
  and raises `check_violation` if the document's `(jurisdiction,
  sector)` is outside the caller's subscription scope.
- Owner of the trigger function is `admin_bypass` (not `schema_owner`):
  the function reads `documents`, which has `FORCE RLS` and policies
  only on `api_app` / `ingestion_worker`. Under `schema_owner` the
  `EXISTS` would see zero rows. `admin_bypass` has `BYPASSRLS` + a
  static `SELECT ON documents` grant (since WU1.4), making it the
  minimal owner that can see the row. Inline-documented in the
  migration.
- The migration also grants `admin_bypass` `USAGE` on schema
  `app_private` and `EXECUTE` on `app_private.current_scope()` — both
  needed so the trigger function (owned by `admin_bypass`) can be
  created and can call the helper at fire time.

### Repo + DTO updates

- `WatchlistDTO` gains `document_id: uuid.UUID`; `WatchlistsRepository.create`
  takes `*, document_id: uuid.UUID` (alongside the existing
  keyword-only `user_id`). Other repo methods (`list_for`,
  `get_by_id`, `delete`) unchanged.
- `horizons_core.repos.users.UsersRepository.get_by_id` already shipped
  in WU4.2; reused by the `/v1/me` route.

### Subscription-summary helpers (`horizons_core.core.subscriptions`)

A small new module:

- `current_scope_pairs(session)` — returns the bound caller's
  `(jurisdiction, sector)` set via
  `SELECT … FROM app_private.current_scope()`. Service-layer scope
  check + `/v1/me` summary both call it.
- `current_subscription_summary(session)` — composes the scope set
  plus the caller's active `subscriptions` rows into a
  `SubscriptionSummaryDTO` for the `/v1/me` response.

Both use the SQLAlchemy expression layer (`func.app_private.current_scope().table_valued(...)`,
`cast(func.current_setting('app.user_id'), UUID)`) instead of
`sqlalchemy.text(...)` so the `text()` carve-out (single-file allow-list
in `db/session.py`) stays intact.

### Routes

- `routes/me.py` — replaces the WU4.1 stub with the real implementation:
  fetches the user row through `UsersRepository`, attaches the
  subscription summary, sets `Cache-Control: private, no-store`. The
  WU4.1 path stays stable so the SPA's `useAuthStore.fetchMe()` (WU5.0)
  can wire to it now without a rename.
- `routes/watchlists.py` (new) — three endpoints under
  `/v1/me/watchlists`:
  - `GET ""` — lists owner's watchlists (RLS filters).
  - `POST ""` — body `{document_id, name?}`. **Service-layer scope
    check first**: looks the document up via the RLS-narrowed
    `DocumentsRepository.get_by_id`; if `None`, returns 422 (covers
    both "not visible" and "absent"). Belt-and-braces second check
    against `current_scope_pairs`. Both raise the same
    `"document is outside your subscription scope"` body so a client
    can't probe which guard fired. `name` defaults to the document's
    title when omitted.
  - `DELETE "/{watchlist_id}"` — 204 on success, 404 when RLS makes
    the row invisible (not 403; row existence is not leaked).

All three responses carry `Cache-Control: private, no-store`.

### Tests (+9 integration, +1 trigger / migration / RLS test backfills)

- `tests/test_me_and_watchlists_endpoints.py` — 8 tests:
  - `test_get_me_returns_user_and_subscription_summary` — real shape:
    user_id / email / role / created_at / subscription{scope[],
    active_subscriptions[]}; `Cache-Control: private, no-store`.
  - `test_list_watchlists_initially_empty_with_cache_header`.
  - `test_post_watchlist_in_scope_returns_201_and_persists`.
  - `test_post_watchlist_out_of_scope_returns_422_service_layer` —
    the service-layer happy path: the route returns 422 *before*
    reaching the DB.
  - **`test_trigger_rejects_out_of_scope_insert_defence_in_depth`** —
    the defence-in-depth half the WU acceptance asks for: drives
    `WatchlistsRepository.create` directly through `session_for_user`
    + `SET LOCAL ROLE api_app`, bypassing the route's check. The
    database trigger raises `IntegrityError` with
    `"outside subscription scope"`.
  - `test_delete_own_watchlist_returns_204`.
  - `test_delete_others_watchlist_returns_404`.
  - `test_v1_me_still_rejects_refresh_token` — regression on the kind
    gate that landed in WU4.1's fix-up.
- WU1.4 `watchlists` tests refactored to seed a document FK target and
  (for the cross-client INSERT test) a covering subscription so the
  trigger passes and the `WITH CHECK` is the failing layer the test
  is about. New WU4.3 test `test_watchlists_user_document_unique_constraint`
  asserts the new `UNIQUE (user_id, document_id)`.
- WU1.6 `test_repos_watchlists.py` gains a
  `_make_document_and_subscription` helper; each test seeds the FK
  target + scope under superuser.
- WU1.5 `test_session_bracket.py` gains `_seed_doc_and_scope` and now
  inserts watchlists with a document.
- WU1.7 `tests/isolation/conftest.py` — `_make_watchlist` takes a
  `document_id`; each client's watchlist is keyed to their own
  scope's document.
- `tests/test_current_scope_migration.py::test_current_scope_execute_granted_to_api_app_only`
  updated: `admin_bypass` now also has EXECUTE (the trigger needs to
  call it). Comment in the test explains why.

### Doc updates

- `docs/api/auth.md` (landed with WU4.2) already states the
  `Cache-Control: private, no-store` posture for per-user responses;
  WU4.3 inherits it.

## Design decisions worth keeping

1. **Watchlist = (user, document, name)**, not a free-form saved
   query. The plan's WU4.3 acceptance and WU5.2's "add / remove
   watched documents" point at this shape; the `name` column is kept
   (defaults to the document title) for future renaming but is not
   load-bearing. The earlier "saved query / filter" framing is
   superseded.
2. **Service-layer 422 + database `check_violation` trigger, in that
   order**. The plan's `… at the service layer; trigger catches
   mismatches` matches this two-layer posture. The trigger is a hard
   stop for any path that goes around the API service (admin scripts,
   future bulk-import jobs, accidental repository-only callers); the
   service layer is what produces a clean validation error rather than
   a 500.
3. **Trigger silently short-circuits when `app.user_id` is unset**.
   The migration tests and admin / migration paths run as superuser
   with no GUC; making the trigger require a GUC would force every
   test to set up a subscription before any watchlist insert. Better:
   gate by "is there application-session context at all?" so the
   trigger is precisely the defence for the api_app path.
4. **Trigger function owner is `admin_bypass`, not `schema_owner`**.
   `FORCE RLS` on `documents` plus api_app-only policies means
   `schema_owner` sees zero rows under SECURITY DEFINER. `admin_bypass`
   has `BYPASSRLS` and an explicit `SELECT ON documents` grant since
   WU1.4. The trigger's scope is narrow (it only checks `NEW.document_id`
   against the bound caller's scope), so the BYPASSRLS surface does
   not leak through.
5. **422 covers both "out of scope" and "document does not exist".**
   `DocumentsRepository.get_by_id` is RLS-narrowed: a document the
   caller cannot see returns `None` regardless of whether the row
   exists. Distinguishing the two on the wire would leak whether
   particular documents exist in *anyone's* scope. Same posture as
   the delete-404 → row-existence-not-leaked decision.
6. **`current_subscription_summary` uses the SQLAlchemy expression
   layer, not `sqlalchemy.text(...)`.** The `text()` carve-out in
   `tests/test_raw_sql_isolation.py` is a structural rule. Reaching
   for the expression layer (`func.app_private.current_scope().table_valued(...)`,
   `cast(func.current_setting('app.user_id'), UUID(as_uuid=True))`)
   keeps the rule intact and adds zero behavioural risk.
7. **No CSRF token on the auth-flow endpoints (yet).** The webapp's
   refresh cookie is `SameSite=Lax + Path=/v1/auth`. SameSite=Lax is
   defence enough for the demo because the auth endpoints accept
   *POST* only and Lax already blocks the third-party-form-submit
   class. Stronger CSRF (token or double-submit) lands when WU5.0
   wires the SPA login flow; flagging now so it doesn't slip.

## Gotchas hit during implementation

- **`SECURITY DEFINER` runs under the function owner, which is
  subject to RLS unless the owner has `BYPASSRLS`.** First version of
  the trigger function set `OWNER TO schema_owner`; the EXISTS query
  saw zero documents because `documents` has `FORCE RLS` and only
  api_app / ingestion_worker have policies. Fix: own the function as
  `admin_bypass`. Required adding `USAGE` on the schema and `EXECUTE`
  on `current_scope()` for that role (with matching `REVOKE` in the
  downgrade).
- **`from datetime import datetime` cannot be moved under
  `TYPE_CHECKING` in a Pydantic model module.** Pydantic resolves the
  annotation at construction time and raises
  `PydanticUserError: class not fully defined` if `datetime` is only
  a string. Ruff's `TC003` push-into-TYPE_CHECKING was correct for
  most modules but wrong here — added `# noqa: TC003` with the reason
  inline.
- **Test assertion update for `current_scope_execute` grants**.
  `test_current_scope_execute_granted_to_api_app_only` previously
  asserted `admin_bypass_can_execute is False`. WU4.3's trigger needs
  the grant; updated the assertion + an inline comment explaining the
  flip.
- **`SET LOCAL ROLE` is not subject to RLS** — verified that even
  with the role grant chain extended for `admin_bypass`, the cross-user
  RLS tests in `test_rls_watchlists.py` still demonstrate B not seeing
  A's row (RLS narrows the SELECT regardless of grants).

## Status by suite (end of WU4.3)

- 408 default-marker tests passing (was 400 → +8 endpoint integration
  tests; the watchlist migration / RLS / repo / session-bracket /
  isolation suites have new assertions but the same passing counts).
- ruff check / ruff format: clean.
- pyright strict: 0 errors.
- pre-commit all-files: clean.
- Webapp gate (`lint:check` / `build` / `vitest --run`): clean.

## Track 4 status

| WU | Status |
| --- | --- |
| WU4.0 | shipped |
| WU4.1 | shipped |
| WU4.2 | shipped |
| **WU4.3** | **shipped (`routes/me.py` real, `routes/watchlists.py`, `core/subscriptions.py`, `migration 0009`)** |
| WU4.4 | next — `/v1/discovery`, `/v1/temporal`, `/v1/differential` (depends on WU3.4 + WU4.1) |
| WU4.5 | depends on WU4.3 + WU1.9 — admin subscription endpoints |

WU4.4, WU4.5, and WU4.6 are the remaining Track-4 deliverables. WU4.4
needs WU3.4 (alignment results landed in the corpus tables) — already
shipped per the ingestion-track journal — so all three are ready to
start.

## Cadence note

Worktree `wu4.2-4.3-auth-endpoints-and-watchlists` carries both WU4.2
and WU4.3. The merge to `main` lands them together via fast-forward
per `CLAUDE.md`'s CI / merge cadence.
