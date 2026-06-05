import { expect, test } from '@playwright/test'

/**
 * WU8.2 — end-to-end smoke for the demo's headline UX.
 *
 * Asserts:
 * 1. UK client login → /changes shows the UK MODIFIED event (confidence
 *    0.92, green badge) and DOES NOT show the EU event or the
 *    suppressed-by-default UK MOVED event.
 * 2. Clicking the UK row lands on /changes/:id with the before/after text
 *    rendered in the diff view and a green "0.92" badge.
 * 3. Logout → /login.
 * 4. EU client login → /changes shows the EU MODIFIED event (confidence
 *    0.78, amber badge) and DOES NOT show the UK event.
 * 5. Clicking the EU row shows the amber "0.78" badge.
 *
 * The asymmetric visibility in steps 1 and 4 is the proof of subscription-
 * scoped RLS at the browser layer.
 */

// example.com is RFC-2606 reserved; matches seed_e2e.py (the .test TLD is
// rejected by pydantic's EmailStr as a special-use name).
const UK_EMAIL = 'uk-client@e2e.example.com'
const UK_PASSWORD = 'e2e-test-pass-uk'
const EU_EMAIL = 'eu-client@e2e.example.com'
const EU_PASSWORD = 'e2e-test-pass-eu'

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

  // -------- 3. UK clause diff --------
  await ukRow.click()
  await page.waitForURL('**/changes/*')
  await expect(page.getByTestId('path-display')).toContainText(UK_PATH)
  await expect(page.locator('[data-confidence="high"]')).toHaveText('0.92')
  await expect(page.locator('body')).toContainText(UK_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(UK_AFTER_FRAGMENT)

  // -------- 4. Logout --------
  await page.getByTestId('back-to-changes').click()
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

  // -------- 7. EU clause diff --------
  await euRow.click()
  await page.waitForURL('**/changes/*')
  await expect(page.getByTestId('path-display')).toContainText(EU_PATH)
  await expect(page.locator('[data-confidence="medium"]')).toHaveText('0.78')
  await expect(page.locator('body')).toContainText(EU_BEFORE_FRAGMENT)
  await expect(page.locator('body')).toContainText(EU_AFTER_FRAGMENT)
})
