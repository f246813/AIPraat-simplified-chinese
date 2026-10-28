import { test, expect, type Page } from '@playwright/test';

const transcript = (page: Page) => page.locator('.transcript');
// Physical mount: only rows in the viewport (plus overscan) are in the DOM. This is deliberately
// separate from the logical range the reveal button describes, which is how far up the reader has
// expanded. Proving the two stay decoupled is the point of virtualisation.
const mounted = (page: Page) => page.locator('.message').count();
const reveal = (page: Page) => page.locator('.history-reveal button');
const mountedIds = (page: Page) => page.evaluate(() => Array.from(document.querySelectorAll('[data-message-id]'), n => n.getAttribute('data-message-id')).filter((id): id is string => id !== null));
const offsetOf = (page: Page, id: string) => transcript(page).evaluate((el, mid) => {
  const row = document.querySelector(`[data-message-id="${mid}"]`) as HTMLElement | null;
  return row ? row.getBoundingClientRect().top - el.getBoundingClientRect().top : Number.NaN;
}, id);
const scrollTop = (page: Page) => transcript(page).evaluate(el => Math.round(el.scrollTop));

async function openLongHistory(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
}

test('a long history opens on the latest reply but mounts only the viewport, not the window', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  // The opened logical range is 60, so a reader at the tail sees 61 withheld above them.
  await expect(reveal(page)).toContainText('显示更早的 61 条消息');
  // The newest row is the reading target, so it is mounted at the bottom of the range.
  expect((await mountedIds(page)).at(-1)).toBe('diagram-fixture');
  // History older than the logical range is not mounted at all.
  expect(await page.locator('[data-message-id="history-0"]').count()).toBe(0);
  // Only a viewport-sized slice is mounted — far below the 60-row logical range and 121 total.
  expect(await mounted(page)).toBeGreaterThan(0);
  expect(await mounted(page)).toBeLessThan(40);
  expect(errors).toEqual([]);
});

test('a short history is never windowed, so nothing is hidden from the reader', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '演示旧记录'}).click();
  await expect(page.locator('.message')).toHaveCount(2);
  await expect(reveal(page)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('revealing an earlier page widens the logical range without mounting it all', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await expect(reveal(page)).toContainText('显示更早的 61 条消息');

  await reveal(page).evaluate(el => (el as HTMLElement).click());
  // The logical range grew by a step: 60 → 100, so 40 more rows are now reachable above.
  await expect(reveal(page)).toContainText('显示更早的 21 条消息');
  // Yet the physical mount never tracked that growth — the reader stays at the tail.
  expect(await mounted(page)).toBeLessThan(40);
  expect((await mountedIds(page)).at(-1)).toBe('diagram-fixture');
  expect(errors).toEqual([]);
});

test('reaching the top reveals the next page and holds the reading position', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  // Reaching the top of the opened range pulls the older page in on its own.
  await transcript(page).evaluate(el => { el.scrollTop = 0; });
  await expect(reveal(page)).toContainText('显示更早的 21 条消息');

  // The row now at the reading line keeps its place when the next page is prepended above it.
  const topRow = (await mountedIds(page)).find(id => id.startsWith('history-'))!;
  const before = await offsetOf(page, topRow);
  await reveal(page).evaluate(el => (el as HTMLElement).click());
  await expect(reveal(page)).toHaveCount(0);
  await expect.poll(async () => Math.abs(await offsetOf(page, topRow) - before), {timeout: 3000}).toBeLessThanOrEqual(1.5);
  // Prepending older content must not bounce the reader back down to the tail.
  expect((await mountedIds(page)).at(-1)).not.toBe('diagram-fixture');
  expect(errors).toEqual([]);
});

test('the mount recycles in both directions as the reader scrolls', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  // Tail: newest mounted, the oldest loaded row and the mid-history fixtures are not.
  expect((await mountedIds(page)).at(-1)).toBe('diagram-fixture');
  expect(await page.locator('[data-message-id="history-59"]').count()).toBe(0);

  // Scroll up through the whole range: the mid fixture mounts, the tail recycles out of view.
  await transcript(page).evaluate(el => { el.scrollTop = 0; });
  await expect(reveal(page)).toContainText('显示更早的 21 条消息');
  for (let round = 0; round < 4 && await reveal(page).count(); round++) {
    await transcript(page).evaluate(el => { el.scrollTop = 0; });
    await page.waitForTimeout(250);
  }
  await transcript(page).evaluate(el => { el.scrollTop = 0; });
  await expect.poll(() => scrollTop(page), {timeout: 3000}).toBeLessThan(50);
  await expect.poll(() => page.locator('[data-message-id="history-0"]').count()).toBeGreaterThan(0);
  expect(await page.locator('[data-message-id="diagram-fixture"]').count()).toBe(0);

  // And back down: the newest re-mounts while the oldest is dropped again.
  await transcript(page).evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect.poll(() => page.locator('[data-message-id="diagram-fixture"]').count()).toBeGreaterThan(0);
  expect((await mountedIds(page)).at(-1)).toBe('diagram-fixture');
  expect(errors).toEqual([]);
});
test('every mounted row carries its place in the session, not its place in the mount', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  // The mount is a viewport slice of a 121-message session, so an assistive reader still hears 121 of
  // 121 at the newest row: numbering counts the history, never the rows that happen to be in the DOM.
  const newest = page.locator('[role="listitem"]', {has: page.locator('[data-message-id="diagram-fixture"]')});
  await expect(newest).toHaveAttribute('aria-posinset', '121');
  await expect(newest).toHaveAttribute('aria-setsize', '121');
  // Every mounted row agrees with its own place in the session, not with its rank among mounted rows.
  const numbering = await transcript(page).evaluate(el => Array.from(el.querySelectorAll('[role="listitem"]')).map(row => ({
    id: row.querySelector('[data-message-id]')?.getAttribute('data-message-id') || '',
    posinset: Number(row.getAttribute('aria-posinset')),
    setsize: Number(row.getAttribute('aria-setsize')),
  })));
  expect(numbering.length).toBeGreaterThan(0);
  for (const row of numbering) {
    expect(row.setsize).toBe(121);
    expect(row.posinset).toBe(row.id === 'diagram-fixture' ? 121 : Number(row.id.replace('history-', '')) + 1);
  }

  // Opening another page of history leaves both numbers alone.
  await reveal(page).evaluate(el => (el as HTMLElement).click());
  await expect(newest).toHaveAttribute('aria-posinset', '121');
  expect(await page.locator('[role="listitem"]').count()).toBeLessThan(40);
  expect(errors).toEqual([]);
});

test('the keyboard opens withheld pages and the rows it arrives at are focusable', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  expect(await page.evaluate(() => document.activeElement?.className)).not.toContain('transcript');
  await transcript(page).focus();

  // Focus makes the reading region scrollable by key, so PageUp is the reader's own scrolling:
  // reaching the top of the opened range reveals the older page, page after page, and the reading
  // line stays where it was — so keep keying until both the range is whole and the head is in view.
  for (let press = 0; press < 90; press++) {
    await page.keyboard.press('PageUp');
    await page.waitForTimeout(20);
    if (await scrollTop(page) < 20 && !(await reveal(page).count())) break;
  }
  await expect(reveal(page)).toHaveCount(0);
  await expect.poll(() => page.locator('[data-message-id="history-0"]').count()).toBeGreaterThan(0);
  // Arriving is mounting: the head of the session is real DOM, its rows still carry session numbering,
  // and one of them takes focus — none of that existed when the session opened.
  const headRows = await transcript(page).evaluate(el => Array.from(el.querySelectorAll('[role="listitem"]')).map(row => ({
    id: row.querySelector('[data-message-id]')?.getAttribute('data-message-id') || '',
    posinset: Number(row.getAttribute('aria-posinset')),
    buttons: row.querySelectorAll('button').length,
  })));
  expect(headRows.some(row => row.id === 'history-0' && row.posinset === 1)).toBe(true);
  // The earliest row near the head that has its own action is the one the reader can now reach by key.
  const reachable = headRows.filter(row => row.buttons > 0 && row.posinset <= 25).sort((a, b) => a.posinset - b.posinset)[0];
  expect(reachable).toBeDefined();
  const action = page.locator('[role="listitem"]', {has: page.locator(`[data-message-id="${reachable.id}"]`)}).locator('button').first();
  await action.focus();
  await expect(action).toBeFocused();
  expect(await mounted(page)).toBeLessThan(40);
  expect(errors).toEqual([]);
});
