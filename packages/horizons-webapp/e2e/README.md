# Horizons end-to-end smoke (WU8.2)

A single Playwright test that gates merges to main before the 2026-06-08 demo.
The flow is the demo's headline UX:

1. Login as UK client → land on `/`
2. Navigate to `/changes` → assert UK MODIFIED row visible, EU/MOVED hidden
3. Click row → land on `/documents/:id` (the side-by-side viewer); assert
   the URL carries `before=` and `after=` query params, the document title
   is visible, and both before and after clause text appear; toggle structure
   mode and assert the highlighted clause card is visible
4. Logout → redirected to `/login`
5. Login as EU client → land on `/`
6. Navigate to `/changes` → assert EU MODIFIED row visible (different scope
   to UK proves subscription RLS at the browser layer)
7. Click row → land on `/documents/:id`; assert URL params, before/after EU
   clause text, and highlighted clause card in structure mode

The two test users live under `@e2e.example.com` so they're trivially
distinguishable from any demo or production accounts. (`example.com` is
RFC-2606 reserved; `.test` would have been more idiomatic but pydantic's
`EmailStr` validator rejects it as a special-use TLD.)

## Local boot sequence

Boot Postgres + API + webapp following
[`docs/runbooks/local-dev.md`](../../../docs/runbooks/local-dev.md) with
three e2e-specific substitutions:

1. **Seed with `seed_e2e.py`**, not `seed_curated_set.py` — the e2e
   asserts against the two-tenant UK/EU fixture that script writes.

   ```bash
   uv run python packages/horizons-api/scripts/seed_e2e.py
   ```

2. **Build + preview the webapp**, not `npm run dev`. Playwright
   targets a production-shaped bundle so the dev HMR runtime can't
   confound it.

   ```bash
   cd packages/horizons-webapp
   npm run build
   npx vite preview --port 5173
   ```

3. **Run the test in a third terminal** (after the API is healthy and
   the preview is up):

   ```bash
   cd packages/horizons-webapp
   npx playwright install --with-deps chromium   # first time only
   npm run test:e2e
   ```

## Cleanup

```bash
HORIZONS_DB_URL=... uv run python packages/horizons-api/scripts/seed_e2e.py --teardown
docker rm -f horizons-pg
```

## CI

`.github/workflows/e2e.yml` runs the same flow against a Postgres 18 service
container on every push and PR. Job timeout is 10 minutes. On failure the
workflow uploads `playwright-report/` and `test-results/` as artefacts.

## Selectors used (don't break these without updating the test)

| Selector | Component | What it asserts |
| --- | --- | --- |
| `[data-testid="email-input"]` / `password-input` / `login-submit` | `LoginView.vue` | Login form |
| `[data-testid="sign-out"]` | `AppNavBar.vue` | Logout button (rendered by the shared navbar across all customer views) |
| `[data-testid="nav-changes"]` | `AppNavBar.vue` | "Browse recent changes" link |
| `[data-testid="change-row"]` | `ChangesView.vue` | One row per visible event |
| `[data-change-type="MODIFIED"]` etc. | `ChangeTypePill.vue` | Change-type filter / count |
| `[data-confidence="high"]` / `medium` / `low` | `ConfidenceBadge.vue` | Badge tier |
| `[data-testid="not-found-state"]` | `DocumentDetailView.vue` | Out-of-scope document landed here |
| `[data-testid="document-title"]` | `DocumentDetailView.vue` | Document heading visible after navigation |
| `[data-testid="version-pane-header"]` | `VersionPane.vue` | Header label for each version pane (v1 / v2) |
| `[data-testid="side-by-side"]` | `DocumentDetailView.vue` | Wrapper for the two-pane side-by-side layout |
| `[data-testid="toggle-structure"]` | `DocumentDetailView.vue` | Button that shows/hides the clause structure overlay |
| `[data-testid="document-body"]` | `ClauseOverlay.vue` | Rendered clause content body |
