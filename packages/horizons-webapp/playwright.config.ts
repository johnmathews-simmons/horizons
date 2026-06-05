import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright config for the WU8.2 end-to-end smoke test.
 *
 * The test boots against a fully-running stack: Postgres + alembic-migrated
 * schema + seeded e2e fixtures + uvicorn + vite preview. See
 * `e2e/README.md` for the local boot sequence and `.github/workflows/e2e.yml`
 * for the CI variant.
 *
 * Chromium-only by default for speed — adding firefox/webkit projects is a
 * post-demo follow-up. The webapp uses standard DOM APIs (no Chrome-only
 * features), so cross-browser is cheap when it's wanted.
 */
const BASE_URL = process.env.HORIZONS_E2E_BASE_URL ?? 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // The test seeds shared DB rows; one worker keeps the assertions deterministic.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // The webapp + API are expected to be already running when `playwright
  // test` is invoked. CI starts them in earlier workflow steps; local devs
  // run them in side terminals (see `e2e/README.md`). We deliberately do
  // not use Playwright's `webServer` because the API isn't a Playwright-
  // managed process — it needs Postgres up first.
})
