import { expect, test } from '@playwright/test'

/**
 * WU8.5 — end-to-end smoke for the documents viewer + clause-structure toggle.
 *
 * Asserts:
 * 1. UK client login → /documents lists at least the UK e2e document and
 *    NOT the EU one (subscription RLS at the browser layer).
 * 2. Open the document → the page renders body text in reader mode.
 * 3. Toggle "Show clause structure" → clause cards appear with anchor chips
 *    that match the parser's clause paths.
 * 4. Toggle off → cards disappear; reader mode is back.
 */

const UK_EMAIL = 'uk-client@e2e.example.com'
const UK_PASSWORD = 'e2e-test-pass-uk'

const UK_DOC_TITLE = 'UK Banking Act (sample, e2e)'
const EU_DOC_TITLE = 'EU Banking Directive (sample, e2e)'
const UK_CLAUSE_PATH = 'PART_2/SECTION_12'

test('UK client browses documents, opens one, toggles the clause structure', async ({
  page,
}) => {
  // -------- Login --------
  await page.goto('/login')
  await page.getByTestId('email-input').fill(UK_EMAIL)
  await page.getByTestId('password-input').fill(UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  // -------- Documents list --------
  await page.goto('/documents')

  const ukRow = page.getByTestId('document-row').filter({ hasText: UK_DOC_TITLE })
  await expect(ukRow).toBeVisible()

  // EU document is invisible to UK by subscription scope.
  await expect(
    page.getByTestId('document-row').filter({ hasText: EU_DOC_TITLE }),
  ).toHaveCount(0)

  // -------- Document detail --------
  await ukRow.click()
  await page.waitForURL('**/documents/*')
  await expect(page.getByTestId('document-title')).toHaveText(UK_DOC_TITLE)

  // Reader mode by default: clauses run together, no anchor cards.
  await expect(page.getByTestId('clause-card')).toHaveCount(0)
  await expect(page.getByTestId('document-body')).toContainText('Capital requirements')

  // -------- Toggle structure on --------
  await page.getByTestId('toggle-structure').click()
  const cards = page.getByTestId('clause-card')
  await expect(cards.first()).toBeVisible()
  // The parser-assigned anchor path is visible as a chip.
  await expect(
    page.getByTestId('clause-anchor').filter({ hasText: UK_CLAUSE_PATH }),
  ).toBeVisible()

  // -------- Toggle off --------
  await page.getByTestId('toggle-structure').click()
  await expect(page.getByTestId('clause-card')).toHaveCount(0)
})
