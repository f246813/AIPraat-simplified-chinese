import { test, expect, type Page } from '@playwright/test';
import { revealAllHistory } from './reveal';

const openOnMessage = async (page: Page, id: string, button: 'right' = 'right') => {
  await page.locator(`[data-message-id="${id}"] .message-content`).click({button});
  await expect(page.getByRole('menu')).toBeVisible();
};

/**
 * Expand the logical range to the first message, then put the reading position at its top so the
 * virtual engine mounts it. Revealing older history widens the range; only scrolling decides what
 * is in the DOM, so reaching a row means both steps have to happen.
 */
async function openOnOldestMessage(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  await revealAllHistory(page);
  const transcript = page.locator('.transcript');
  await transcript.evaluate(el => { el.scrollTop = 0; });
  await expect(page.locator('[data-message-id="history-0"]')).toBeAttached({timeout: 5000});
  // The row must be comfortably in view before it can be clicked.
  await page.locator('[data-message-id="history-0"]').evaluate(el => el.scrollIntoView({block: 'center'}));
  await expect.poll(async () => page.locator('[data-message-id="history-0"]').evaluate(el => {
    const box = el.getBoundingClientRect();
    const viewport = document.querySelector('.transcript')!.getBoundingClientRect();
    return box.top >= viewport.top - 2 && box.bottom <= viewport.bottom + 2;
  })).toBe(true);
}

test('a message row offers only the actions this host can carry out', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openOnOldestMessage(page);

  await openOnMessage(page, 'history-0');
  const labels = await page.getByRole('menuitem').allInnerTexts();
  expect(labels).toEqual(['复制消息', '选择消息文本']);

  // The tool row additionally exposes its structured evidence, and nothing else.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
  const transcript = page.locator('.transcript');
  await transcript.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached({timeout: 5000});
  await page.locator('[data-message-id="diagram-fixture"]').evaluate(el => el.scrollIntoView({block: 'center'}));
  await openOnMessage(page, 'diagram-fixture');
  expect(await page.getByRole('menuitem').allInnerTexts()).toEqual(['复制消息', '选择消息文本', '复制结构化证据']);
  // Menu mutation of history is not offered because no RPC can honour it.
  for (const label of ['编辑', '删除', '重新生成', '分支']) await expect(page.getByRole('menuitem', {name: label})).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('the transcript background offers the conversation actions and the selection actions work', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();

  const transcript = page.locator('.transcript');
  const box = (await transcript.boundingBox())!;
  // Dispatched on the scroller itself: that is the transcript background, not a row.
  const openBackground = async () => {
    await transcript.dispatchEvent('contextmenu', {clientX: box.x + box.width / 2, clientY: box.y + box.height / 2, bubbles: true});
    await expect(page.getByRole('menu')).toBeVisible();
  };
  await openBackground();
  // Selecting text is named for what it actually covers: the rows currently mounted, never the
  // withheld history. Full-session copy is a separate, data-driven entry.
  expect(await page.getByRole('menuitem').allInnerTexts()).toEqual(['复制整个会话', '选择当前显示内容', '跳到顶部', '回到最新']);

  // Selecting the displayed text must reach the platform selection, not a private copy.
  await page.getByRole('menuitem', {name: '选择当前显示内容'}).click();
  const selected = await page.evaluate(() => window.getSelection()?.toString() ?? '');
  expect(selected).toContain('演示历史问题');
  expect(selected.length).toBeGreaterThan(100);
  // Only mounted rows are in the DOM, so the selection cannot silently claim the withheld head.
  expect(selected).not.toContain('演示历史问题 1\n');

  await openBackground();
  await page.getByRole('menuitem', {name: '跳到顶部'}).click();
  // The start of the conversation means the first message row sits at the top of the reading area.
  // The raw scroller offset is not that line: the list starts below the transcript's own padding, so
  // scrolling to 0 would stop short of the first row's top by exactly that much empty space.
  await expect(page.locator('[data-message-id="history-0"]')).toBeAttached({timeout: 5000});
  await expect.poll(async () => {
    const row = (await page.locator('[data-message-id="history-0"]').boundingBox())!;
    const area = (await transcript.boundingBox())!;
    return Math.abs(row.y - area.y);
  }).toBeLessThanOrEqual(2);
  await openBackground();
  await page.getByRole('menuitem', {name: '回到最新'}).click();
  await expect.poll(() => transcript.evaluate(el => Math.round(el.scrollHeight - el.clientHeight - el.scrollTop))).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});
