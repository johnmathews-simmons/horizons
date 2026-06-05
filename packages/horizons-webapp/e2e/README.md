# Horizons end-to-end smoke (WU8.2)

A single Playwright test that gates merges to main before the 2026-06-08 demo.
The flow is the demo's headline UX:

1. Login as UK client → land on `/`
2. Navigate to `/changes` → assert UK MODIFIED row visible, EU/MOVED hidden
3. Click row → assert clause diff view shows the before/after text and a
   green `0.92` confidence badge
4. Logout → redirected to `/login`
5. Login as EU client → land on `/`
6. Navigate to `/changes` → assert EU MODIFIED row visible (different scope
   to UK proves subscription RLS at the browser layer)
7. Click row → assert amber `0.78` confidence badge

The two test users live under `@e2e.example.com` so they're trivially
distinguishable from any demo or production accounts. (`example.com` is
RFC-2606 reserved; `.test` would have been more idiomatic but pydantic's
`EmailStr` validator rejects it as a special-use TLD.)

## Local boot sequence

The test expects the API on `http://localhost:8000` and the webapp on
`http://localhost:5173`. Both must be running before `playwright test` starts.

```bash
# 0. From the repo root.
cd /Users/john/projects/syncthing/agent-lxc/horizons

# 1. Start a Postgres 18 (uuidv7() is a v18 built-in).
docker run --rm -d --name horizons-e2e-pg \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  postgres:18-alpine

export HORIZONS_DB_URL='postgresql+psycopg://postgres:postgres@localhost:5432/postgres'

# 2. Migrate.
uv run alembic upgrade head

# 3. Seed the e2e fixtures (idempotent — re-running wipes prior fixtures).
uv run packages/horizons-api/scripts/seed_e2e.py

# 4. Start uvicorn in terminal A. The CORS origin and JWT keys must be set.
#    For a one-shot local run, any valid PEM key works — the integration
#    tests' RSA fixture is fine. See tests/conftest.py for an example.
export HORIZONS_CORS_ORIGINS='http://localhost:5173'
export HORIZONS_JWT_ISSUER='horizons-e2e'
export HORIZONS_JWT_AUDIENCE='horizons-e2e'
# (export HORIZONS_JWT_PRIVATE_KEY_PEM / HORIZONS_JWT_PUBLIC_KEY_PEM too)
uv run uvicorn horizons_api.app:create_app --factory --port 8000

# 5. Start the webapp preview in terminal B.
cd packages/horizons-webapp
npm ci                                  # if you haven't already
npm run build
npx vite preview --port 5173

# 6. Run the test in terminal C.
cd packages/horizons-webapp
npx playwright install --with-deps chromium   # first time only
npm run test:e2e
```

## Cleanup

```bash
HORIZONS_DB_URL=... uv run packages/horizons-api/scripts/seed_e2e.py --teardown
docker rm -f horizons-e2e-pg
```

## CI

`.github/workflows/e2e.yml` runs the same flow against a Postgres 18 service
container on every push and PR. Job timeout is 10 minutes. On failure the
workflow uploads `playwright-report/` and `test-results/` as artefacts.

## Selectors used (don't break these without updating the test)

| Selector | Component | What it asserts |
| --- | --- | --- |
| `[data-testid="email-input"]` / `password-input` / `login-submit` | `LoginView.vue` | Login form |
| `[data-testid="sign-out"]` | `HomeView.vue` | Logout button on `/` |
| `[data-testid="change-row"]` | `ChangesView.vue` | One row per visible event |
| `[data-change-type="MODIFIED"]` etc. | `ChangeTypePill.vue` | Change-type filter / count |
| `[data-confidence="high"]` / `medium` / `low` | `ConfidenceBadge.vue` | Badge tier |
| `[data-testid="path-display"]` | `ChangeDetailView.vue` | Clause path on the detail page |
| `[data-testid="not-found-state"]` | `ChangeDetailView.vue` | Out-of-scope event landed here |
| `[data-testid="back-to-changes"]` | `ChangeDetailView.vue` | Back link |
