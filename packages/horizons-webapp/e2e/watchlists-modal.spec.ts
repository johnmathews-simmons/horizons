import { expect, test } from '@playwright/test'

/**
 * Regression: the add-documents modal must contain its document list.
 *
 * The bug (fixed in commit 3f84516): reka-ui's DialogContent uses
 * `display: grid`, where children default to `min-width: auto` (not 0).
 * The discovery list inside the modal therefore expanded to its
 * children's max-content width, pushing long-title rows well past the
 * modal's right edge. Fix added `min-w-0` to the content wrapper, the
 * `<ul>`, and the `<li>`, and swapped `truncate` for `break-words` on
 * the title so multi-line wrapping is visible.
 *
 * This test injects a very long synthetic title into the first
 * discovery row (because CI only seeds two short-title documents) and
 * asserts:
 *   1. The row's bounding box stays within the dialog's right edge.
 *   2. The row's rendered height > one line, proving the title wrapped
 *      rather than overflowed or truncated.
 */

const DEMO_UK_EMAIL = 'demo-uk@demo.example.com'
const DEMO_UK_PASSWORD = process.env['HORIZONS_DEMO_UK_PASSWORD'] ?? 'demo-uk-pass-not-secret'

const LONG_TITLE =
  'Latvijas Atveseļošanas un noturības mehānisma plāna otrās komponentes ' +
  '"Digitālā transformācija" 2.3. reformu un investīciju virziena ' +
  '"Digitālās prasmes" 2.3.2.reformas "Digitālās prasmes iedzīvotājiem ' +
  'tostarp seniorā vecuma cilvēkiem" īstenošanas kārtība'

test('add-documents modal: long-title rows stay within the modal width and wrap', async ({
  page,
}) => {
  await page.goto('/login')
  await page.getByTestId('email-input').fill(DEMO_UK_EMAIL)
  await page.getByTestId('password-input').fill(DEMO_UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  await page.goto('/watchlists')
  await page.getByTestId('open-add-dialog').click()

  // Modal + at least one discovery row visible.
  const dialog = page.getByTestId('add-watchlist-dialog').locator('xpath=..')
  await expect(dialog).toBeVisible()
  const row = page.getByTestId('discovery-row').first()
  await expect(row).toBeVisible()

  // Replace the row's title text with a known-long string so the
  // assertion exercises the long-title path regardless of which
  // documents happen to be seeded.
  await row.evaluate((node, title) => {
    const titleDiv = node.querySelector('label > div.flex-1 > div:first-child')
    if (titleDiv) titleDiv.textContent = title
  }, LONG_TITLE)

  const dialogBox = await dialog.boundingBox()
  const rowBox = await row.boundingBox()
  expect(dialogBox).not.toBeNull()
  expect(rowBox).not.toBeNull()
  if (!dialogBox || !rowBox) return

  // Row's right edge must not poke past the dialog's right edge.
  // 1px tolerance for sub-pixel rounding.
  const dialogRight = dialogBox.x + dialogBox.width
  const rowRight = rowBox.x + rowBox.width
  expect(rowRight).toBeLessThanOrEqual(dialogRight + 1)

  // Row must have wrapped onto multiple lines (single-line height is
  // ~36px including padding; a wrapped 4+ line title is >70px).
  expect(rowBox.height).toBeGreaterThan(50)
})
