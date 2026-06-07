import { expect, test } from '@playwright/test'

/**
 * WU8.2 — end-to-end smoke for the demo's headline UX.
 *
 * Asserts:
 * 1. UK client login → /changes shows the UK MODIFIED event (confidence
 *    0.92, green badge) and DOES NOT show the EU event or the
 *    suppressed-by-default UK MOVED event.
 * 2. Clicking the UK row lands on /documents/:id (the side-by-side
 *    viewer) with the before and after clause text both visible.
 * 3. Logout → /login.
 * 4. EU client login → /changes shows the EU MODIFIED event (confidence
 *    0.78, amber badge) and DOES NOT show the UK event.
 * 5. Clicking the EU row lands on /documents/:id with the EU before
 *    and after clause text both visible.
 *
 * The asymmetric visibility in steps 1 and 4 is the proof of subscription-
 * scoped RLS at the browser layer.
 *
 * WU8.5 — home-overview tests (Tasks 12):
 * 6. Demo UK client login → / shows the home dashboard with exactly one
 *    subscribed jurisdiction card containing "UK", plus unsubscribed muted
 *    cards; clicking the UK card navigates to /changes?jurisdiction=UK.
 * 7. Demo admin login → / shows all jurisdiction cards as subscribed (no
 *    data-subscribed="false" cards); clicking "Browse recent changes" link
 *    navigates to /changes and change rows are present.
 *
 * The demo accounts (demo-*@demo.example.com) are provisioned by
 * create_demo_accounts.py, not seed_e2e.py. The e2e CI workflow must run
 * that script before these tests execute.  Passwords are read from env vars
 * with the same defaults baked into create_demo_accounts.py for local runs.
 */

// example.com is RFC-2606 reserved; matches seed_e2e.py (the .test TLD is
// rejected by pydantic's EmailStr as a special-use name).
const UK_EMAIL = 'uk-client@e2e.example.com'
const UK_PASSWORD = 'e2e-test-pass-uk'
const EU_EMAIL = 'eu-client@e2e.example.com'
const EU_PASSWORD = 'e2e-test-pass-eu'

// Demo accounts (seeded by create_demo_accounts.py, not seed_e2e.py).
// Passwords fall back to the baked-in defaults so local runs work without
// extra env setup; CI overrides via HORIZONS_DEMO_*_PASSWORD secrets.
const DEMO_UK_EMAIL = 'demo-uk@demo.example.com'
const DEMO_UK_PASSWORD = process.env['HORIZONS_DEMO_UK_PASSWORD'] ?? 'demo-uk-pass-not-secret'
const DEMO_ADMIN_EMAIL = 'admin-demo@demo.example.com'
const DEMO_ADMIN_PASSWORD =
  process.env['HORIZONS_DEMO_ADMIN_PASSWORD'] ?? 'admin-demo-pass-not-secret'

const UK_PATH = 'Part 2 / Section 12'
const EU_PATH = 'Article 4 / Clause 4.2'
const UK_MOVED_PATH_FRAGMENT = 'Part 3 / Section 14'

const UK_BEFORE_FRAGMENT = '8 percent of risk-weighted assets'
const UK_AFTER_FRAGMENT = '10.5 percent of risk-weighted assets'
const EU_BEFORE_FRAGMENT = '80 percent of net cash outflows'
const EU_AFTER_FRAGMENT = '100 percent of net cash outflows'

test.describe.configure({ mode: 'serial' })

test('UK + EU clients see disjoint clause-diff views', async ({ page }) => {
  // -------- 1. UK login --------
  await page.goto('/login')
  await page.getByTestId('email-input').fill(UK_EMAIL)
  await page.getByTestId('password-input').fill(UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')
  await expect(page.getByTestId('sign-out')).toBeVisible()

  // -------- 2. UK /changes --------
  await page.goto('/changes')

  const ukRow = page.getByTestId('change-row').filter({ hasText: UK_PATH })
  await expect(ukRow).toBeVisible()
  await expect(ukRow.locator('[data-change-type="MODIFIED"]')).toBeVisible()
  await expect(ukRow.locator('[data-confidence="high"]')).toHaveText('0.92')

  // EU event is invisible to UK by subscription scope.
  await expect(
    page.getByTestId('change-row').filter({ hasText: EU_PATH }),
  ).toHaveCount(0)
  // UK MOVED is suppressed by the default-off "Show MOVED" toggle.
  await expect(
    page.getByTestId('change-row').filter({ hasText: UK_MOVED_PATH_FRAGMENT }),
  ).toHaveCount(0)

  // -------- 3. UK clause in document context --------
  await ukRow.click()
  await page.waitForURL('**/documents/**')
  // Document title visible (regardless of single- or two-pane layout).
  await expect(page.getByTestId('document-title')).toBeVisible()
  // Both before and after clause text are visible somewhere on the page
  // (e2e seed creates one v1 with the before text and one v2 with the
  // after text — both render in the side-by-side viewer).
  await expect(page.locator('body')).toContainText(UK_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(UK_AFTER_FRAGMENT)

  // -------- 4. Logout --------
  await page.getByTestId('nav-changes').click()
  await page.waitForURL('**/changes')
  await page.goto('/')
  await page.getByTestId('sign-out').click()
  await page.waitForURL('**/login')

  // -------- 5. EU login --------
  await page.getByTestId('email-input').fill(EU_EMAIL)
  await page.getByTestId('password-input').fill(EU_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  // -------- 6. EU /changes --------
  await page.goto('/changes')

  const euRow = page.getByTestId('change-row').filter({ hasText: EU_PATH })
  await expect(euRow).toBeVisible()
  await expect(euRow.locator('[data-change-type="MODIFIED"]')).toBeVisible()
  await expect(euRow.locator('[data-confidence="medium"]')).toHaveText('0.78')

  // UK event must NOT bleed into the EU view.
  await expect(
    page.getByTestId('change-row').filter({ hasText: UK_PATH }),
  ).toHaveCount(0)

  // -------- 7. EU clause in document context --------
  await euRow.click()
  await page.waitForURL('**/documents/**')
  await expect(page.getByTestId('document-title')).toBeVisible()
  await expect(page.locator('body')).toContainText(EU_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(EU_AFTER_FRAGMENT)
})

test('demo-uk home dashboard: subscribed jurisdiction card + navigation', async ({ page }) => {
  // -------- 1. Login as demo UK client --------
  await page.goto('/login')
  await page.getByTestId('email-input').fill(DEMO_UK_EMAIL)
  await page.getByTestId('password-input').fill(DEMO_UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  // -------- 2. Home dashboard renders --------
  // Wait for the overview data to load (past the loading spinner).
  await expect(page.getByTestId('overview-summary')).toBeVisible()

  // -------- 3. Exactly one subscribed jurisdiction card containing "UK" --------
  const subscribedCards = page.locator('[data-testid="jurisdiction-card"][data-subscribed="true"]')
  await expect(subscribedCards).toHaveCount(1)
  await expect(subscribedCards.first()).toContainText('UK')

  // -------- 4. At least one muted (unsubscribed) card --------
  const unsubscribedCards = page.locator(
    '[data-testid="jurisdiction-card"][data-subscribed="false"]',
  )
  await expect(unsubscribedCards).not.toHaveCount(0)

  // -------- 5. Click UK card → /changes?jurisdiction=UK --------
  await subscribedCards.first().click()
  await page.waitForURL('**/changes?jurisdiction=UK')

  // Filter chip is visible and shows "UK".
  const filterChip = page.getByTestId('changes-filter-chip')
  await expect(filterChip).toBeVisible()
  await expect(filterChip).toContainText('UK')

  // -------- 6. Sign out --------
  await page.goto('/')
  await page.getByTestId('sign-out').click()
  await page.waitForURL('**/login')
})

test('demo-admin home dashboard: all jurisdictions subscribed + nav-changes', async ({ page }) => {
  // -------- 1. Login as demo admin --------
  await page.goto('/login')
  await page.getByTestId('email-input').fill(DEMO_ADMIN_EMAIL)
  await page.getByTestId('password-input').fill(DEMO_ADMIN_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  // -------- 2. Home dashboard renders --------
  await expect(page.getByTestId('overview-summary')).toBeVisible()

  // -------- 3. No unsubscribed cards (admin sees full corpus as subscribed) --------
  await expect(
    page.locator('[data-testid="jurisdiction-card"][data-subscribed="false"]'),
  ).toHaveCount(0)

  // -------- 4. Admin sees both UK and EU cards --------
  // The e2e seed only creates UK + EU documents (seed_e2e.py), so the
  // corpus has exactly 2 jurisdictions in CI. The point of this assertion
  // is corpus-wide visibility: admin sees BOTH of them, whereas the UK
  // client (test at line 61) only sees one. Locally with the demo seed
  // there will be more cards; that's fine — both UK and EU still appear.
  await expect(
    page.locator('[data-testid="jurisdiction-card"][data-code="UK"]'),
  ).toBeVisible()
  await expect(
    page.locator('[data-testid="jurisdiction-card"][data-code="EU"]'),
  ).toBeVisible()

  // -------- 5. "Browse recent changes" nav link → /changes --------
  await page.getByTestId('nav-changes').click()
  await page.waitForURL('**/changes')

  // At least one change row is visible (seeded corpus).
  const changeRows = page.getByTestId('change-row')
  await expect(changeRows.first()).toBeVisible()
})
