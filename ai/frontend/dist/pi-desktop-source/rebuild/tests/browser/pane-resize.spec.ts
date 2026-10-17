import { test, expect, type Page } from '@playwright/test';

const sidebarWidth = (page: Page) => page.locator('.sidebar').evaluate(el => Math.round(el.getBoundingClientRect().width));
const bandWidth = (page: Page) => page.locator('.chat-width-band').evaluate(el => Math.round(el.getBoundingClientRect().width));
const conversationWidth = (page: Page) => page.locator('.thread').evaluate(el => Number.parseFloat(getComputedStyle(el).getPropertyValue('--conversation-width')));
/** The expanded sidebar animates over 180ms, so assert the settled width, not a frame in between. */
const settledSidebar = (page: Page, expected: number) => expect.poll(() => sidebarWidth(page), {timeout: 4000}).toBe(expected);

async function drag(page: Page, selector: string, deltaX: number) {
  const box = await page.locator(selector).boundingBox();
  if (!box) throw new Error(`no box for ${selector}`);
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + deltaX, y, {steps: 6});
  await page.mouse.up();
}

test('the expanded sidebar is width-adjustable by pointer, keyboard and double-click, and collapses instead of cramping', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1');
  await expect(page.locator('.sidebar-resizer')).toHaveCount(0);
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await settledSidebar(page, 272);
  const separator = page.locator('.sidebar-resizer');
  await expect(separator).toHaveAttribute('aria-valuemin', '240');
  await expect(separator).toHaveAttribute('aria-valuenow', '272');

  await drag(page, '.sidebar-resizer', 60);
  await settledSidebar(page, 332);
  await expect(separator).toHaveAttribute('aria-valuenow', '332');
  // The chat column keeps a 450px floor, so the live budget caps the sidebar.
  await drag(page, '.sidebar-resizer', 4000);
  await expect(separator).toHaveAttribute('aria-valuenow', '520');

  await separator.dblclick();
  await settledSidebar(page, 272);

  await separator.focus();
  await page.keyboard.press('ArrowRight');
  await settledSidebar(page, 288);
  await page.keyboard.press('Shift+ArrowRight');
  await settledSidebar(page, 320);
  await page.keyboard.press('ArrowLeft');
  await settledSidebar(page, 304);
  await page.keyboard.press('Home');
  await settledSidebar(page, 272);

  // Dragging past the collapse threshold leaves the rail, not a cramped column.
  await drag(page, '.sidebar-resizer', -400);
  await expect(page.getByRole('button', {name: '展开会话侧栏'})).toBeVisible();
  await settledSidebar(page, 68);
  // The preferred expanded width survives the collapse.
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await settledSidebar(page, 272);
  await page.getByRole('button', {name: '折叠会话侧栏'}).click();
  await expect(page.locator('.sidebar-resizer')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('the conversation band is width-adjustable from either edge and stays inside the pane gutters', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1');
  await expect(page.locator('.chat-width-handle-right')).toHaveCount(1);
  expect(await conversationWidth(page)).toBe(850);
  expect(await bandWidth(page)).toBe(850);

  // Both handles move one centered band, so 50px of pointer travel is 100px of width.
  await drag(page, '.chat-width-handle-right', 50);
  expect(await conversationWidth(page)).toBe(950);
  await drag(page, '.chat-width-handle-left', -50);
  expect(await conversationWidth(page)).toBe(1050);
  expect(await bandWidth(page)).toBe(1050);

  const right = page.locator('.chat-width-handle-right');
  await expect(right).toHaveAttribute('aria-valuemin', '560');
  await right.focus();
  await page.keyboard.press('ArrowLeft');
  expect(await conversationWidth(page)).toBe(1034);
  await page.keyboard.press('Home');
  expect(await conversationWidth(page)).toBe(720);
  await page.keyboard.press('End');
  const pane = await page.locator('.transcript').evaluate(el => el.clientWidth);
  expect(await conversationWidth(page)).toBe(pane - 48);
  await page.keyboard.press('Home');

  await right.dblclick();
  expect(await conversationWidth(page)).toBe(720);
  // A narrow window compresses the drawn band without rewriting the preference.
  await page.setViewportSize({width: 820, height: 700});
  await expect.poll(() => bandWidth(page)).toBeLessThan(850);
  expect(await conversationWidth(page)).toBe(720);
  expect(errors).toEqual([]);
});
