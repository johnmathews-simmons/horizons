# Home overview dashboard — design

*Last revised: 2026-06-06.*
*Path: docs/superpowers/specs/260606-home-overview-design.md.*

**Date:** 2026-06-06
**Status:** approved (brainstorming round); plan pending.
**Driver:** demo needs the post-login landing page to make subscription scoping visible at a glance and to give admins a corpus-wide entry point.

## Goal

Replace the placeholder `HomeView` with a dashboard that summarises the corpus the caller can access. The page makes the (jurisdiction × sector) scoping rule visible — including which jurisdictions / sectors the caller is *not* subscribed to — and routes the caller into `/changes` filtered by the card they click. Admins see the same view with full-corpus counts and no "not subscribed" badges; their `/changes` view returns the entire corpus.

## Non-goals

- No "Subscribe to view" CTA wired up (post-demo).
- No new filter axes on `/changes` beyond `jurisdiction` and `sector`.
- No homepage-level search.
- No change to watchlists or alerts in this work unit.

## UI

`HomeView.vue` — single view, two render branches keyed on `auth.isAdmin`.

Header bar: existing "Browse recent changes" and "Manage watchlists" CTAs move into the compact header next to user email + Sign out.

Body, top to bottom:

1. **Summary row.** Client branch: two stat cards — "Jurisdictions" showing `subscribed / total_in_corpus`, "Sectors" showing `subscribed / total_in_corpus`. Admin branch: one card — "Full corpus access" with total document count.
2. **Jurisdictions section.** Heading "Jurisdictions" + grid of cards, one per jurisdiction in the corpus. Each card: jurisdiction code (e.g. "UK"), document count, "Not subscribed" badge if applicable. Subscribed cards: enabled hover state, click → `router.push({ name: 'changes', query: { jurisdiction: code } })`. Not-subscribed cards: muted styling, click disabled, hover tooltip "Subscribe to view". Admin: all cards enabled, no badges.
3. **Sectors section.** Same pattern, click → `/changes?sector=<code>`.

Two new presentation components: `<JurisdictionCard>` and `<SectorCard>` in `packages/horizons-webapp/src/components/`. Tooltip via existing shadcn `Tooltip`.

## API — new endpoint

`GET /v1/me/overview` — registered in `packages/horizons-api/src/horizons_api/routes/me.py`.

Response shape:

```json
{
  "is_admin": false,
  "totals": {
    "documents": 10,
    "jurisdictions": 8,
    "sectors": 5,
    "subscribed_jurisdictions": 1,
    "subscribed_sectors": 1
  },
  "jurisdictions": [
    { "code": "IE", "document_count": 1, "subscribed": false },
    { "code": "UK", "document_count": 1, "subscribed": true }
  ],
  "sectors": [
    { "code": "BANKING", "document_count": 5, "subscribed": true },
    { "code": "employment", "document_count": 2, "subscribed": false }
  ]
}
```

Headers: `Cache-Control: private, no-store` (same posture as `/v1/me`).

Lists are sorted by `code` ascending. `is_admin` mirrors `principal.role == 'admin'`. For admin callers every `subscribed` is `true` and the `subscribed_*` totals equal their non-subscribed counterparts.

## Backend — corpus shape aggregation

Add a Postgres function `app_public.corpus_shape()` returning rows of `(jurisdiction text, sector text, document_count bigint)` covering every `(jurisdiction, sector)` pair present in `documents`. The function is `SECURITY DEFINER`, owned by a role that can read `documents` unscoped, and `EXECUTE` is granted to `api_app`.

Rationale: corpus shape (which jurisdictions and sectors exist and how many docs each holds) is **non-sensitive catalog data**. Clients already know the subscription token vocabulary because it's part of their own scope contract. Routing this through `admin_bypass` per request would force an audit-log entry for every page load — overkill for catalog data. A dedicated `SECURITY DEFINER` function documents the intent ("this is meant to be unscoped") and keeps RLS strict for actual corpus rows.

Migration: new Alembic revision under `packages/horizons-core/migrations/versions/` creating the function + grant. Document the security posture in `packages/horizons-core/src/horizons_core/db/roles.md`.

Route logic in `me.py`:

1. Call `app_public.corpus_shape()` — full corpus matrix.
2. Call `current_scope_pairs(session)` from `horizons_core.core.subscriptions` — caller's `(j, s)` set.
3. Roll matrix up by jurisdiction and by sector; mark `subscribed=true` where the jurisdiction (resp. sector) appears in any subscribed `(j, s)` pair, OR the caller is admin.
4. Compute totals.

## Backend — admin-aware session for primitives

Problem: `session_for_request` in `packages/horizons-api/src/horizons_api/deps/session.py` pins every request to the `api_app` role, so RLS narrows admin requests too. Today an admin GETting `/v1/discovery` sees nothing (they have no subscription rows).

Fix: add an `admin_or_app_session` dependency in the same module. Behaviour by `principal.role`:

- `client`: identical to `session_for_request` (`api_app`, bound `app.user_id`).
- `admin`: assume `admin_bypass` and write one `admin_access_log` row with `path=request.url.path` (and method) per request, matching the WU1.9 audit shape used by `admin_session` in `packages/horizons-core/src/horizons_core/core/auth/admin.py`.

Apply `admin_or_app_session` to: `/v1/discovery`, `/v1/temporal`, `/v1/differential`, `/v1/differential/{id}`, and the new `/v1/me/overview`. `/v1/me` itself stays on plain `session_for_request` (admin's own row is fine to read under api_app; no scope expansion needed).

This is a small, scoped escalation: admins now see corpus-wide change events through the public primitives, and every escalation is audited.

## Webapp wiring

New composable `packages/horizons-webapp/src/composables/useMeOverview.ts` using `useQuery` with key `['me', 'overview']`. Returns the overview shape; staleTime tuned to ~30s.

`HomeView.vue` rebuild:
- Read `auth.isAdmin` from the auth store.
- Render skeleton while loading, error card on failure.
- Render summary row + two sections from the overview response.
- Card click handlers push to named `changes` route with `jurisdiction` or `sector` query.

`useChangeEvents` composable extended to accept `{ jurisdiction?: string; sector?: string }`. Filters become part of `queryKey` and the `fetchDiscovery` call. ChangesView reads `useRoute().query` for `jurisdiction` / `sector`, passes them into `useChangeEvents`, and renders a small "Filtered by: UK ✕" chip when present (✕ clears the query param).

## Tests

**API (pytest, testcontainers Postgres):**
- `/v1/me/overview` — UK client (`UK / BANKING`): `is_admin=false`, totals reflect the seed, exactly one jurisdiction card and one sector card marked `subscribed=true`.
- `/v1/me/overview` — EU client (`EU / BANKING`): one jurisdiction (`EU`) and one sector (`BANKING`) subscribed; EU jurisdiction `document_count == 2` (two relabelled fixtures).
- `/v1/me/overview` — admin: `is_admin=true`, every `subscribed=true`, totals match corpus counts.
- `/v1/discovery` under `admin_or_app_session`: admin without a subscription gets non-empty results; one `admin_access_log` row written per request; client behaviour unchanged.

**Webapp (Vitest):**
- `HomeView` renders the mocked overview correctly for client and admin variants.
- Click on subscribed card pushes the expected route with query.
- Click on not-subscribed card does not push; tooltip renders.

**Playwright e2e (`packages/horizons-webapp/e2e/login-and-scope.spec.ts` extension):**
- `demo-uk` lands on `/`: sees 1 subscribed UK card + 9 muted; click UK → `/changes?jurisdiction=UK` shows only UK rows.
- `admin-demo` lands on `/`: all cards enabled, no badges; "Browse recent changes" lists corpus-wide rows from multiple jurisdictions.

## Files touched

- New: `packages/horizons-core/migrations/versions/<rev>_corpus_shape_function.py`
- New: `packages/horizons-api/src/horizons_api/routes/me.py` (add `/v1/me/overview`; existing `/v1/me` unchanged)
- New: `packages/horizons-webapp/src/composables/useMeOverview.ts`
- New: `packages/horizons-webapp/src/components/JurisdictionCard.vue`
- New: `packages/horizons-webapp/src/components/SectorCard.vue`
- Edit: `packages/horizons-api/src/horizons_api/deps/session.py` (add `admin_or_app_session`)
- Edit: `packages/horizons-api/src/horizons_api/routes/primitives.py` (swap dependency)
- Edit: `packages/horizons-webapp/src/views/HomeView.vue` (full rebuild)
- Edit: `packages/horizons-webapp/src/views/ChangesView.vue` (read query, filter chip)
- Edit: `packages/horizons-webapp/src/composables/useChangeEvents.ts` (filter args)
- Edit: `packages/horizons-core/src/horizons_core/db/roles.md` (document `app_public.corpus_shape`)
- Edit: `docs/api/horizons-primitives.md` + regenerated `docs/api/endpoints.md`
- Edit: `packages/horizons-webapp/e2e/login-and-scope.spec.ts`

## Open questions

None blocking. Card grid breakpoints (2 / 3 / 4 columns) decided in implementation.
