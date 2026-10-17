import { test, expect, type Page, type Locator } from '@playwright/test';
import { revealAllHistory } from './reveal';

// Native CSS scroll anchoring would mask the explicit anchor, so these cases disable it.
// That is also the configuration the mount window will need, because prepending history
// has to be compensated deliberately rather than by the browser.
const disableNativeAnchoring = (page: Page) => page.addStyleTag({content: '.transcript{overflow-anchor:none !important}'});
const early = (page: Page) => page.locator('[data-message-id="history-39"] .process-details');
const held = (page: Page) => page.locator('[data-message-id="history-59"] .process-details');
const heldSummary = (page: Page) => held(page).locator(':scope > summary');
const heldOffset = (page: Page) => heldSummary(page).evaluate(el => {
  const scroller = document.querySelector('.transcript')!;
  return el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
});
const scrollState = (page: Page) => page.locator('.transcript').evaluate(el => ({top: Math.round(el.scrollTop), max: Math.round(el.scrollHeight - el.clientHeight)}));
/**
 * Fixture manipulation, deliberately not a click: clicking a row arms the anchor on
 * *that* row, so a change above the held row has to come from somewhere else — which is
 * exactly the case the anchor exists for (streaming growth, another card's state change).
 */
const setOpen = (page: Page, locator: Locator, open: boolean) => locator.evaluate((el, value) => { (el as HTMLDetailsElement).open = value as boolean; }, open);
const heightOf = (page: Page, locator: Locator) => locator.evaluate(el => el.getBoundingClientRect().height);

async function openLongHistory(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await disableNativeAnchoring(page);
  await revealAllHistory(page);
  await expect(held(page)).toBeAttached();
}

/** Park the held row mid-viewport and register a real scroll gesture, so the viewport is reading rather than following. */
async function parkHeldRow(page: Page) {
  await heldSummary(page).evaluate(el => el.scrollIntoView({block: 'center'}));
  const box = await page.locator('.transcript').boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.wheel(0, -40);
  await page.waitForTimeout(250);
}

test('height changing above the held row is compensated, not passed on to the reading position', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  await setOpen(page, early(page), true);
  await expect(early(page)).toHaveAttribute('open', '');
  await page.waitForTimeout(200);
  const earlyExpanded = await heightOf(page, early(page));

  await parkHeldRow(page);
  // Guard against the vacuous case: pinned at the bottom, the browser's own clamp
  // would hold the content and this test would pass without any anchor at all.
  const state = await scrollState(page);
  expect(state.max - state.top).toBeGreaterThan(200);

  // A real click on the visible row arms the hold. Its own expansion is downward, so the row must not move.
  const before = await heldOffset(page);
  await heldSummary(page).click();
  await expect(held(page)).toHaveAttribute('open', '');
  await page.waitForTimeout(250);
  expect(Math.abs(await heldOffset(page) - before)).toBeLessThanOrEqual(2);

  // Now remove height above the held row without touching its handler: the reading position must absorb it.
  await setOpen(page, early(page), false);
  await expect(early(page)).not.toHaveAttribute('open', '');
  await page.waitForTimeout(350);

  expect(earlyExpanded - await heightOf(page, early(page))).toBeGreaterThan(20);
  expect(Math.abs(await heldOffset(page) - before)).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});

test('expanding a row still lets the content below it move down', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  await parkHeldRow(page);
  const rowBefore = await heldOffset(page);
  const belowBefore = await page.locator('[data-message-id="history-60"]').evaluate(el => {
    const scroller = document.querySelector('.transcript')!;
    return el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  });
  const collapsedHeight = await held(page).evaluate(el => el.getBoundingClientRect().height);

  await heldSummary(page).click();
  await expect(held(page)).toHaveAttribute('open', '');
  await page.waitForTimeout(350);

  // The clicked row holds its own position...
  expect(Math.abs(await heldOffset(page) - rowBefore)).toBeLessThanOrEqual(2);
  // ...so the anchor must not suppress the natural growth underneath it.
  const grew = await held(page).evaluate(el => el.getBoundingClientRect().height) - collapsedHeight;
  expect(grew).toBeGreaterThan(5);
  const belowAfter = await page.locator('[data-message-id="history-60"]').evaluate(el => {
    const scroller = document.querySelector('.transcript')!;
    return el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  });
  expect(belowAfter - belowBefore).toBeGreaterThan(grew - 3);
  expect(errors).toEqual([]);
});
