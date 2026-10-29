import { test, expect, type Page } from '@playwright/test';

const bar = (page: Page) => page.locator('.chat-search');
const field = (page: Page) => page.getByRole('searchbox', {name: '在会话中查找'});
const status = (page: Page) => page.locator('.chat-search-count');
const transcript = (page: Page) => page.locator('.transcript');
const highlightSizes = (page: Page) => page.evaluate(() => {
  const api = (CSS as unknown as {highlights?: Map<string, {size: number}>}).highlights;
  return {all: api?.get('transcript-search')?.size ?? 0, active: api?.get('transcript-search-active')?.size ?? 0};
});
/**
 * The active hit as the reader experiences it: the row that owns it, the text it covers, and whether
 * its box is inside the transcript viewport. Identified by row rather than by index because a
 * virtualised list changes what is mounted underneath the navigation.
 */
const activeHit = (page: Page) => page.evaluate(() => {
  const api = (CSS as unknown as {highlights?: Map<string, Set<Range>>}).highlights;
  const range = api?.get('transcript-search-active')?.values().next().value;
  if (!range) return null;
  const viewport = document.querySelector('.transcript')!.getBoundingClientRect();
  const rect = (range as Range).getBoundingClientRect();
  const start = range.startContainer;
  const row = ((start instanceof Element ? start : start.parentElement))?.closest('[data-message-id]');
  const prefix = document.createRange();
  if (row) { prefix.selectNodeContents(row); prefix.setEnd(range.startContainer, range.startOffset); }
  return {messageId: row?.getAttribute('data-message-id') ?? '', offset: prefix.toString().length, text: range.toString(), inView: rect.top >= viewport.top - 2 && rect.bottom <= viewport.bottom + 2};
});

async function openLongHistory(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
}

test('Mod+K opens the find bar, and hits are painted without rewriting the transcript', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await expect(bar(page)).toHaveCount(0);

  await page.keyboard.press('Control+k');
  await expect(bar(page)).toBeVisible();
  await expect(field(page)).toBeFocused();

  // Exactly one mounted row carries this string, so the count is deterministic.
  await field(page).fill('未投递');
  await expect(status(page)).toHaveText('1/1');
  expect(await highlightSizes(page)).toEqual({all: 1, active: 1});
  // The highlight is painted by the CSS Custom Highlight API, not by wrapping text in the DOM.
  await expect(page.locator('.transcript-content mark')).toHaveCount(0);
  await expect(transcript(page)).toContainText('未投递');

  await field(page).fill('夹具');
  await expect(status(page)).not.toHaveText('1/1');
  const many = await highlightSizes(page);
  expect(many.all).toBeGreaterThan(5);
  expect(many.active).toBe(1);

  // Escape closes the bar and releases the highlights.
  await page.keyboard.press('Escape');
  await expect(bar(page)).toHaveCount(0);
  expect(await highlightSizes(page)).toEqual({all: 0, active: 0});
  expect(errors).toEqual([]);
});

test('next and previous move between hits and bring them into view', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await page.keyboard.press('Control+k');
  await field(page).fill('夹具');
  await expect(status(page)).toHaveText(/^\d+\/\d+$/);

  // The active hit is identified by the row that owns it and the text it covers, and "bring it
  // into view" means its box sits inside the transcript viewport.
  const before = await activeHit(page);
  expect(before).not.toBeNull();
  // A new query settles on a match already on screen instead of moving the reading position.
  expect(before!.inView).toBe(true);

  await page.getByRole('button', {name: '下一个匹配'}).click();
  await expect.poll(() => activeHit(page)).not.toEqual(before);
  expect((await activeHit(page))!.inView).toBe(true);

  await page.getByRole('button', {name: '上一个匹配'}).click();
  await expect.poll(() => activeHit(page)).toEqual(before);
  expect((await activeHit(page))!.inView).toBe(true);

  // Keyboard navigation and wrapping are the same code path as the buttons.
  await field(page).press('Enter');
  await expect.poll(() => activeHit(page)).not.toEqual(before);
  await field(page).press('Shift+Enter');
  await expect.poll(() => activeHit(page)).toEqual(before);
  expect(errors).toEqual([]);
});

test('a match outside the mounted slice is offered as a candidate, then counted once it renders', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  // The DOM holds a viewport slice, not the whole expanded logical range.
  expect(await page.locator('.message').count()).toBeLessThan(40);

  await page.keyboard.press('Control+k');
  // history-29 renders the heading 第 30 条夹具; it is neither mounted nor scrolled to.
  await field(page).fill('第 30 条夹具');
  await expect(status(page)).toHaveText('无匹配');
  const hint = page.locator('.chat-search-hint');
  await expect(hint).toBeVisible();
  await expect(hint).toContainText('可能匹配');
  // Nothing is painted while the only match is unrendered source, and no count is invented for it.
  expect(await highlightSizes(page)).toEqual({all: 0, active: 0});

  // Jumping to the candidate expands the logical range, mounts its neighbourhood, and then counts
  // the hit for real — while the mount stays a slice rather than the whole history.
  await hint.getByRole('button').click();
  await expect(page.locator('[data-message-id="history-29"]')).toBeAttached();
  await expect(status(page)).toHaveText('1/1');
  expect(await page.locator('.message').count()).toBeLessThan(40);
  await expect(page.locator('.chat-search-hint')).toHaveCount(0);
  expect((await activeHit(page))!.messageId).toBe('history-29');
  expect(errors).toEqual([]);
});

test('a query with no hits anywhere reports it and offers nothing', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await page.keyboard.press('Control+k');
  await field(page).fill('绝对不存在的字符串');
  await expect(status(page)).toHaveText('无匹配');
  await expect(page.locator('.chat-search-hint')).toHaveCount(0);
  await expect(page.getByRole('button', {name: '下一个匹配'})).toBeDisabled();
  expect(errors).toEqual([]);
});
