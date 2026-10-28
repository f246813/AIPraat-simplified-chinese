import type { Page } from '@playwright/test';

/**
 * Open the logical range until every loaded message is inside it.
 *
 * Not a test file: `tests/browser` only collects `*.spec.ts`. Fixtures above the steady range have
 * to be revealed the way a reader reveals them, so the specs stay honest about what the range does
 * — expanding it never means those rows stay mounted, the viewport still decides that.
 */
export async function revealAllHistory(page: Page) {
  const reveal = page.locator('.history-reveal button');
  for (let round = 0; round < 12; round++) {
    if (!(await reveal.count())) return;
    await reveal.evaluate(el => (el as HTMLElement).click());
    await page.waitForTimeout(150);
  }
  throw new Error('the logical history range never opened fully');
}
