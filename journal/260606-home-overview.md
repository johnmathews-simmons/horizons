# Home overview dashboard

Built the post-login home dashboard. Goal: make subscription scoping
visible at a glance and give admins a corpus-wide landing page.

## What landed

- New `GET /v1/me/overview` returning the corpus matrix grouped by
  jurisdiction and by sector, with `subscribed` flags per item and an
  `is_admin` discriminator.
- New `app_public.corpus_shape()` SECURITY DEFINER function — non-sensitive
  catalog data, no per-request audit. Migration 0013. Function owned by
  `schema_owner`; a sibling `documents_schema_owner_read` RLS policy
  admits the SECURITY DEFINER read against `documents`' `FORCE ROW LEVEL
  SECURITY`.
- New `admin_or_app_session` dependency (the testable bracket) plus
  `admin_or_app_session_dep` (the FastAPI dep) in
  `packages/horizons-api/src/horizons_api/deps/admin_or_app.py`. Client
  callers run under `api_app`; admin callers run under `admin_bypass`
  and write one `admin_access_log` row per request (`reason = request
  path`). Applied to discovery / temporal / differential / overview.
- HomeView rebuilt: summary cards + Jurisdictions / Sectors sections
  with drill-down to `/changes?jurisdiction=...` / `/changes?sector=...`.
  Not-subscribed cards are visibly muted and click-disabled, with a
  "Subscribe to view" tooltip. Admin variant collapses the summary into
  a single "Full corpus" card and removes badges.
- ChangesView reads `jurisdiction` / `sector` from the route query and
  threads them through `useChangeEvents`; a "Filtered by ..." chip with
  a clear button surfaces the active filter.
- Playwright e2e extended: UK demo user sees 1 subscribed + ≥1 muted
  card and a working drill-down; admin sees no muted badges and
  corpus-wide rows on `/changes`.
- `.github/workflows/e2e.yml` now seeds the `@demo.example.com`
  accounts (UK / EU / admin) in addition to the existing
  `@e2e.example.com` accounts, so the new home-dashboard tests can run
  in CI.

## Why this shape

Corpus shape (which jurisdictions / sectors exist, how many docs each)
is catalog data, not tenant data — clients already know the token
vocabulary. Routing it through `admin_bypass` per page load would force
a per-load audit entry for no security gain, so a `SECURITY DEFINER`
function is the right seam. Per-row corpus content stays scoped via
RLS everywhere else.

The bug surfaced during Task 2 is worth recording: `documents` has
`FORCE ROW LEVEL SECURITY` (migration 0005), so even the table owner
(`schema_owner`) is subject to RLS. The new function ran under
`schema_owner` via SECURITY DEFINER, hit RLS with no applicable policy,
and returned zero rows. Fixed in 0013 with a narrow
`documents_schema_owner_read` policy that admits the SECURITY DEFINER
read without widening any other access path.

`admin_or_app_session_dep` is the smallest seam that lets admins use
the public primitives directly. Adding `/v1/admin/discovery` etc. was
the alternative; rejected because it doubles the API surface for one
reader.

## Notable plan-vs-reality deltas

- The plan underspecified test fixture infrastructure for the
  `horizons-core` and `horizons-api` packages. Both packages got a
  new `tests/conftest.py` modelled on the existing root
  `tests/isolation/conftest.py` — testcontainers Postgres 18 → Alembic
  upgrade → role-scoped async sessions. Documented in each conftest's
  docstring; the next plan that adds a per-package integration test
  shouldn't need to redo this.
- The `Role` enum referenced in the plan didn't exist as such; the
  codebase already has `UserRole` as a `StrEnum`. Re-exported as
  `Role` from `horizons_core.core.auth` to satisfy the plan's idiom
  (`principal.role == Role.ADMIN`).
- Migration 0013 grew beyond a single function: needed `CREATE SCHEMA
  IF NOT EXISTS app_public AUTHORIZATION schema_owner`, `REVOKE ALL ON
  SCHEMA ... FROM PUBLIC`, `GRANT USAGE` (for both `api_app` and
  `admin_bypass`), `ALTER FUNCTION ... OWNER TO schema_owner`, plus
  the `documents_schema_owner_read` policy noted above. All required
  by patterns already established in migration 0004 — the code review
  pass at the end of Task 1 caught the gaps before they hit a runtime
  `permission denied` in CI.

## Follow-ups (post-demo)

- Subscribe-to-view CTA on muted cards.
- Webapp fast-check property tests once the dust settles (see
  [[project-horizons-post-demo-fastcheck]]).
- Reconcile the pre-existing `/v1/me` subscription DTO shape mismatch
  between server (`scope` / `active_subscriptions`) and webapp client
  (`active_pairs` / `is_admin_bypass`). Not blocking the demo because
  the new HomeView consumes `/v1/me/overview`, not the legacy
  subscription DTO.
- Cache-buster on `['me', 'overview']` when a subscription changes
  (admin / impersonation paths).
