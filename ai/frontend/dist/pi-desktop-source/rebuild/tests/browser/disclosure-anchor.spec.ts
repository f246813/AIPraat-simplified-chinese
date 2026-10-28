import { test, expect, type Page, type Locator } from '@playwright/test';

// Native CSS scroll anchoring would mask the engine's own compensation, so these cases turn it
// off and also match the configuration the virtualised list runs with: a change in height has to
// be absorbed by the engine's key-anchoring, not handed on to the reading position by the browser.
const disableNativeAnchoring = (page: Page) => page.addStyleTag({content: '.transcript{overflow-anchor:none !important}'});
const scroller = (page: Page) => page.locator('.transcript');
// history-58 sits directly above the held row, history-60 directly below: all three are inside the
// mounted window whenever history-59 is on screen, so the compensation is exercised between rows
// the reader can actually see rather than a pair virtualisation would never co-mount.
const early = (page: Page) => page.locator('[data-message-id="history-58"] .process-details');
const held = (page: Page) => page.locator('[data-message-id="history-59"] .process-details');
const heldSummary = (page: Page) => held(page).locator(':scope > summary');
const offsetOf = (page: Page, id: string) => scroller(page).evaluate((el, mid) => {
  const row = document.querySelector(`[data-message-id="${mid}"]`) as HTMLElement | null;
  return row ? row.getBoundingClientRect().top - el.getBoundingClientRect().top : Number.NaN;
}, id);
/**
 * Fixture manipulation, deliberately not a click: clicking a row arms the anchor on *that* row,
 * so a height change above the held row has to come from somewhere else — which is exactly the
 * case the anchor exists for (streaming growth, another card's state change).
 */
const setOpen = (page: Page, locator: Locator, open: boolean) => locator.evaluate((el, value) => { (el as HTMLDetailsElement).open = value as boolean; }, open);
const heightOf = (page: Page, locator: Locator) => locator.evaluate(el => el.getBoundingClientRect().height);

/**
 * Opens the long history, pulls in the page holding the fixtures, and parks the held row where the
 * case needs it: `reading-line` puts its top edge just under the reading line, so the row before it
 * is entirely above it — the only place a height change belongs to the engine's compensation;
 * `centre` keeps the rows above and below on screen, where growth below the reading line is
 * expected to move the flow. The offset is written directly rather than with `scrollIntoView`,
 * because rows are butt-packed and an exact alignment would leave the neighbour's bottom edge
 * sitting on the reading line by a fraction of a pixel.
 */
async function openAndMountHeld(page: Page, place: 'reading-line' | 'centre') {
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: '展开会话侧栏' }).click();
  await page.locator('.session-select').filter({ hasText: '长历史' }).click();
  await disableNativeAnchoring(page);
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  // Reaching the top of the opened logical range pulls in the next page and mounts the fixture rows.
  await scroller(page).evaluate(el => { el.scrollTop = 0; });
  await expect.poll(() => page.locator('[data-message-id="history-59"]').count()).toBeGreaterThan(0);
  await scroller(page).evaluate((el, mode) => {
    const row = document.querySelector('[data-message-id="history-59"]') as HTMLElement;
    const top = row.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
    el.scrollTop = mode === 'reading-line' ? top + 4 : top - (el.clientHeight - row.getBoundingClientRect().height) / 2;
  }, place);
  await page.waitForTimeout(250);
  // Guard against the vacuous case: at either end of the scroll range the browser's own clamp would
  // hold the content for reasons that have nothing to do with the engine.
  const room = await scroller(page).evaluate(el => ({above: el.scrollTop, below: el.scrollHeight - el.clientHeight - el.scrollTop}));
  expect(room.above).toBeGreaterThan(200);
  expect(room.below).toBeGreaterThan(200);
}

test('height changing above the reading line is compensated, not passed on to the reading position', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openAndMountHeld(page, 'reading-line');

  await setOpen(page, early(page), true);
  await expect(early(page)).toHaveAttribute('open', '');
  await page.waitForTimeout(200);
  const earlyExpanded = await heightOf(page, early(page));
  // The row that changes has to be wholly above the viewport top; a row straddling the reading line
  // grows below it and belongs to the flow instead (the second case covers that).
  expect(await offsetOf(page, 'history-58')).toBeLessThan(-20);

  // The held row must not move when the row above it collapses: the scroller absorbs the height.
  const before = await offsetOf(page, 'history-59');
  await setOpen(page, early(page), false);
  await expect(early(page)).not.toHaveAttribute('open', '');
  await page.waitForTimeout(350);

  expect(earlyExpanded - await heightOf(page, early(page))).toBeGreaterThan(20);
  expect(Math.abs(await offsetOf(page, 'history-59') - before)).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});

test('expanding a row still lets the content below it move down', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openAndMountHeld(page, 'centre');

  const rowBefore = await offsetOf(page, 'history-59');
  const belowBefore = await offsetOf(page, 'history-60');
  const collapsedHeight = await heightOf(page, held(page));

  // A real click on the held row arms the hold on itself; its own growth is downward, so its top
  // must not move, and the anchor must not suppress the natural shift of the rows underneath it.
  await heldSummary(page).click();
  await expect(held(page)).toHaveAttribute('open', '');
  await page.waitForTimeout(350);

  expect(Math.abs(await offsetOf(page, 'history-59') - rowBefore)).toBeLessThanOrEqual(2);
  const grew = await heightOf(page, held(page)) - collapsedHeight;
  expect(grew).toBeGreaterThan(5);
  expect(await offsetOf(page, 'history-60') - belowBefore).toBeGreaterThanOrEqual(grew - 3);
  expect(errors).toEqual([]);
});
