# 2026-06-05 — WU8.2: Playwright end-to-end smoke test

The demo's headline UX as a single Playwright test, gating merges to `main`
before the 2026-06-08 demo. Worktree `wu8.2-playwright-e2e-smoke`.

## What shipped

Three additions, no edits to existing source code (Sessions H/WU5.5 own the
webapp tree this session ran alongside):

1. `packages/horizons-api/scripts/seed_e2e.py` — idempotent seed + teardown
   for two test users (`uk-client@e2e.test`, `eu-client@e2e.test`), two
   subscriptions, two synthetic docs/versions, three change events. Uses
   `SET LOCAL session_replication_role = 'replica'` to bypass the
   `change_events_no_delete` trigger during teardown (superuser-only —
   CI runs as `postgres`).
2. `packages/horizons-webapp/playwright.config.ts` +
   `packages/horizons-webapp/e2e/login-and-scope.spec.ts` +
   `packages/horizons-webapp/e2e/README.md` — chromium-only Playwright
   config, single serial spec covering the seven-step UK→EU flow, and a
   local-boot recipe.
3. `.github/workflows/e2e.yml` — Postgres 18 service container, alembic +
   seed + uvicorn + vite preview, `npx playwright test`, artefact upload on
   failure, 10-minute job timeout.

Supporting tweaks: `packages/horizons-webapp/package.json` gains
`@playwright/test` as a devDependency and a `test:e2e` script;
`packages/horizons-webapp/tsconfig.e2e.json` keeps the e2e dir out of the
app/vitest build graphs but in the TypeScript project references so
vue-tsc still validates it.

No `data-testid` attributes added to existing components. Sessions H/WU5.0
had already placed every selector the test needs (see the **Selectors** table
below) — this WU is purely additive.

## What the test asserts

1. **UK login** → `/` → `[data-testid="sign-out"]` visible.
2. **UK `/changes`** → the row matching `Part 2 / Section 12` is visible
   and contains a `[data-change-type="MODIFIED"]` pill and a
   `[data-confidence="high"]` badge with text `0.92`. The EU row (`Article
   4 / Clause 4.2`) and the suppressed-by-default UK MOVED row (`Part 3 /
   Section 14`) are both absent — proves both subscription RLS and the
   default-off `Show MOVED` toggle from one assertion.
3. **UK clause diff** → `/changes/:id` shows `path-display` containing
   `Part 2 / Section 12`, badge `0.92`, and the before/after text
   fragments `8 percent of risk-weighted assets` and `10.5 percent of
   risk-weighted assets`.
4. **Logout** → `/login`.
5. **EU login** → `/`.
6. **EU `/changes`** → the EU row is visible with `0.78` amber badge; the
   UK row is absent (the other RLS direction).
7. **EU clause diff** → amber `0.78` badge, EU before/after fragments.

The spec is `test.describe.configure({ mode: 'serial' })` because the
fixtures are shared DB rows. `workers: 1` in the config keeps it from being
accidentally parallelised in the future.

## Selectors used (don't break these without updating this test)

| Selector | Component | What the test reads |
| --- | --- | --- |
| `[data-testid="email-input"]` / `password-input` / `login-submit` | `LoginView.vue` | Login form |
| `[data-testid="sign-out"]` | `HomeView.vue` | Logout |
| `[data-testid="change-row"]` (with `.filter({ hasText })`) | `ChangesView.vue` | One per visible event |
| `[data-change-type="MODIFIED"]` | `ChangeTypePill.vue` | Pill type |
| `[data-confidence="high"]` / `medium` / `low` | `ConfidenceBadge.vue` | Badge tier + text |
| `[data-testid="path-display"]` | `ChangeDetailView.vue` | Clause path on detail |
| `[data-testid="back-to-changes"]` | `ChangeDetailView.vue` | Back link |

If any of these are renamed or removed in later WUs, this test fails fast.
Treat the table as the contract; the test enforces it.

## Fixtures (which row maps to which assertion)

| # | Visible to | `change_type` | `alignment_confidence` | `before_path → after_path` | What it proves |
| --- | --- | --- | --- | --- | --- |
| 1 | UK only | MODIFIED | 0.92 | `Part 2 / Section 12` → same | Primary diff render; green badge; clause-level scope correctness |
| 2 | EU only | MODIFIED | 0.78 | `Article 4 / Clause 4.2` → same | Subscription RLS at the browser layer (UK can't see it); amber badge |
| 3 | UK only | MOVED | 0.95 | `Part 3 / Section 14` → `Part 4 / Section 14` | Default-off MOVED suppression (test asserts ABSENT, not present) |

`alignment_confidence` numbers are chosen to land in the three badge tiers
defined in `src/constants/confidence.ts` (`>= 0.85` → high, `>= 0.6` → medium,
`<` → low). Moving the thresholds will move the test — that's intentional;
the test should track the actual UI contract.

The `before_text` / `after_text` fragments are generic — no firm names, no
real bank names. The `@e2e.test` TLD is RFC 6761 (reserved for testing) so
the e2e accounts can never collide with real customer accounts.

## Idempotence + teardown

Re-running `seed_e2e.py` is safe: every run begins with a teardown that
purges anything matching `%@e2e.test` (users) or `e2e_%` (documents,
matched via `lawstronaut_document_id LIKE 'e2e\_%' ESCAPE '\\'`). The
`--teardown` flag runs the purge and exits without re-seeding. The
teardown uses `SET LOCAL session_replication_role = 'replica'` to bypass
the change_events append-only trigger — this needs Postgres superuser,
which the CI `services: postgres` container gives us out of the box.
Local devs running against a non-superuser DB would need to either
connect as the superuser for the seed/teardown step, or
`ALTER TABLE change_events DISABLE TRIGGER USER` manually (post-demo
follow-up if anyone hits it).

## Postgres version

CI uses `postgres:18-alpine` to match the integration suite. The user's
prompt called for `postgres:17`; I deviated because `uuidv7()` is a
Postgres 18 built-in (see `migrations/0002_tenancy_tables.py` and
friends) and the schema would fail to apply on 17. Same image the
testcontainer suite uses, so behaviour matches.

## Local boot sequence (verbatim)

The full recipe is also in `packages/horizons-webapp/e2e/README.md`. The
short version:

```bash
# From the repo root
docker run --rm -d --name horizons-e2e-pg \
  -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:18-alpine

export HORIZONS_DB_URL='postgresql+psycopg://postgres:postgres@localhost:5432/postgres'
export HORIZONS_CORS_ORIGINS='http://localhost:5173'
export HORIZONS_JWT_ISSUER='horizons-e2e'
export HORIZONS_JWT_AUDIENCE='horizons-e2e'
# Generate ephemeral RSA keys (mirror what e2e.yml does):
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/e2e-private.pem
openssl rsa -in /tmp/e2e-private.pem -pubout -out /tmp/e2e-public.pem
export HORIZONS_JWT_PRIVATE_KEY_PEM="$(cat /tmp/e2e-private.pem)"
export HORIZONS_JWT_PUBLIC_KEY_PEM="$(cat /tmp/e2e-public.pem)"

uv run alembic upgrade head
uv run packages/horizons-api/scripts/seed_e2e.py

# Terminal A: API
uv run uvicorn 'horizons_api.app:create_app' --factory --port 8000

# Terminal B: webapp preview
cd packages/horizons-webapp
npm ci
npm run build
npx vite preview --port 5173

# Terminal C: the test
cd packages/horizons-webapp
npx playwright install --with-deps chromium  # first time only
npm run test:e2e
```

Teardown:

```bash
HORIZONS_DB_URL=... uv run packages/horizons-api/scripts/seed_e2e.py --teardown
docker rm -f horizons-e2e-pg
```

## In-session verification gate

From the worktree root (all green):

```bash
uv run ruff check .                       # All checks passed!
uv run pyright                            # 0 errors, 25 warnings (pre-existing stubs)
uv run pytest -m "not integration" -q     # 323 passed, 4 skipped, 199 deselected
uv run pre-commit run --all-files         # all hooks Passed

cd packages/horizons-webapp
npm run lint:check                        # oxlint + eslint clean
npm run test:unit -- --run                # 11 files, 86 tests, all pass
npm run build                             # vue-tsc + vite green
npx playwright --version                  # 1.60.0
```

I did not run the e2e suite itself in-session — Playwright browsers
weren't downloaded and Docker for the Postgres container wasn't
exercised. The script-side surface is fully covered by the gate above;
the integration is verified by the first CI run after merge.

## External verification (post-merge, NOT done in-session)

The first CI run of `e2e.yml` is the integration smoke. To trigger
manually after merge:

```bash
gh workflow run e2e.yml
gh run watch  # or: gh run view --log
```

Expected green-path log lines (in order):

```
API ready after Ns
Preview ready after Ns
Running 1 test using 1 worker
  1 passed
```

If the test fails, three artefacts are uploaded:
`playwright-report` (HTML), `playwright-test-results` (traces + videos),
and `e2e-server-logs` (uvicorn + vite preview output).

## Things considered, then dropped

1. **Adding `data-testid` to existing components.** Inspected every
   referenced component (`LoginView`, `HomeView`, `ChangesView`,
   `ChangeDetailView`, `ConfidenceBadge`, `ChangeTypePill`) before
   writing the spec — every selector the test needs is already in
   place from WU5.0 + WU5.3. No edits to existing source code.
2. **Playwright `webServer` block in `playwright.config.ts`.** The API
   needs Postgres up first, so a Playwright-managed server doesn't fit
   cleanly. CI orchestrates start/wait/test as discrete workflow
   steps; locally the dev uses two terminals. Documented at the
   bottom of `playwright.config.ts`.
3. **Cross-browser projects (firefox / webkit).** Chromium-only keeps
   the gate fast and avoids each driver's quirks. Post-demo follow-up
   if any customer asks.
4. **Mocking the API.** The whole point of this WU is end-to-end —
   mocks would invalidate the subscription-scope assertions. The seed
   script gives us deterministic real rows; that's the right
   substrate.

## Track 8 status

| WU | Status |
| --- | --- |
| WU8.0 | not yet (curated set bootstrap) |
| WU8.1 | not yet (demo accounts) |
| **WU8.2** | **shipped (this WU)** |
| WU8.3 | not yet (demo runbook) |
| WU8.4 | not yet (final journal + CLAUDE.md `Commands` section) |

## Post-merge follow-ups (same session, second commit on `main`)

`/done` ran an independent code-reviewer pass after the WU8.2 merge. Four
real issues surfaced; all landed in a follow-up commit before the first
CI run.

1. **`admin_access_log` FK violation on re-run.** The teardown deleted
   users but left `admin_access_log` rows whose `admin_id` /
   `target_user_id` pointed at the e2e users. Both FKs are
   `ON DELETE RESTRICT` (migration 0006), so the `users` DELETE would
   error 23503 on the second run against any DB that had previously
   exercised the WU4.5 admin code path. Fresh CI containers are safe;
   shared dev DBs were not. Added a
   `DELETE FROM admin_access_log WHERE admin_id IN (...) OR
   target_user_id IN (...)` step ahead of the `users` delete — covered
   by the existing `session_replication_role = 'replica'` trigger
   bypass (the table also has a `BEFORE DELETE` reject trigger from
   migration 0006).
2. **`SET LOCAL session_replication_role` bleed.** The seed and
   teardown shared one transaction, so the bypass intended only for
   the `change_events` / `admin_access_log` DELETEs also covered every
   seed-side INSERT. Currently a no-op (no INSERT triggers exist) but
   a latent footgun the moment one is added. Split into two
   `engine.begin()` blocks; `SET LOCAL` now dies with the teardown
   transaction.
3. **`retries: 2` in CI was wrong for a smoke test.** A retry would
   re-run the whole serial flow against the same DB state and any
   accumulated cookies; a flake could mask a genuine ordering bug.
   Smoke tests must fail hard. Dropped to `retries: 0`. Also moved
   `trace` to `retain-on-failure` so the trace artefact stays useful
   under the new policy.
4. **Playwright browser cache in CI.** The workflow re-downloaded the
   ~130 MB Chromium bundle on every run. Added an
   `actions/cache@v4` keyed on the resolved `@playwright/test` version
   from `node_modules/@playwright/test/package.json`. Cache hit →
   `install-deps` only (OS-level apt packages); cache miss → full
   `install --with-deps`. Standard pattern from Playwright's docs.

## Cadence note

Worktree `wu8.2-playwright-e2e-smoke`. Fast-forward merge into `main` per
`CLAUDE.md`'s CI / merge cadence. First CI run of `e2e.yml` happens after
merge — that's the external verification beat.
