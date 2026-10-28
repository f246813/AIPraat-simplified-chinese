import { test, expect, type Page } from '@playwright/test';

const rail = (page: Page) => page.locator('.minimap-rail');
const dashes = (page: Page) => page.locator('.minimap-marker:not(.history)');
const transcript = (page: Page) => page.locator('.transcript');
const topMessage = (page: Page) => transcript(page).evaluate(el => {
  const top = el.getBoundingClientRect().top;
  const row = Array.from(document.querySelectorAll('[data-message-id]')).find(r => r.getBoundingClientRect().bottom > top) as HTMLElement | undefined;
  return row?.dataset.messageId || '';
});

async function openLongHistory(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '长历史'}).click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
}

const revealHistory = async (page: Page) => {
  await page.locator('.minimap-marker.history').click();
  await expect.poll(async () => (await railState(page)).markers).toBe(100);
};

/** What the rail stands for and whether it still fits inside the track it is drawn in. */
const railState = (page: Page) => page.evaluate(() => {
  const track = document.querySelector('.minimap-rail')!;
  const box = track.getBoundingClientRect();
  const rows = Array.from(document.querySelectorAll<HTMLElement>('.minimap-marker:not(.history)'));
  return {
    dashes: rows.length,
    markers: rows.reduce((total, row) => total + Number(row.dataset.groupSize || 1), 0),
    sizes: rows.map(row => Number(row.dataset.groupSize || 1)),
    // Positive only when a dash is pushed outside the track, which grouping exists to prevent.
    outside: rows.reduce((worst, row) => {
      const dash = row.getBoundingClientRect();
      return Math.max(worst, box.top - dash.top, dash.bottom - box.bottom);
    }, 0),
  };
});

test('the rail covers the expanded history and stays inside its track', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);

  await expect(rail(page)).toBeVisible();
  // A history the track can hold dash-for-dash keeps one dash per message.
  await expect.poll(async () => (await railState(page)).markers).toBe(60);
  expect(await railState(page)).toMatchObject({dashes: 60, outside: 0});
  await expect(dashes(page).first()).toHaveAttribute('data-marker-id', 'history-61');

  // Revealing the withheld page grows what the rail describes; the track decides how.
  await revealHistory(page);
  const revealed = await railState(page);
  expect(revealed.markers).toBe(100);
  expect(revealed.dashes).toBeLessThan(revealed.markers);
  expect(new Set(revealed.sizes)).toEqual(new Set([2]));
  expect(revealed.outside).toBe(0);
  expect(errors).toEqual([]);
});

/** The row the rail's reading line sits on: the last mounted row starting at or above it. */
const readingLineMessage = (page: Page) => transcript(page).evaluate(el => {
  const line = el.getBoundingClientRect().top + el.clientHeight * 0.3;
  const above = Array.from(document.querySelectorAll<HTMLElement>('[data-message-id]')).filter(row => row.getBoundingClientRect().top <= line);
  return above.length ? above[above.length - 1].dataset.messageId || '' : '';
});

test('the active dash follows the reading line and a click jumps to that row', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await expect.poll(async () => (await railState(page)).markers).toBe(60);

  // Mid-range, so the reading line is a mounted row and no page is owed above it.
  await transcript(page).evaluate(el => { el.scrollTop = el.scrollHeight / 2; });
  await expect(page.locator('.minimap-marker.active')).toHaveCount(1);
  await expect(page.locator('.minimap-marker.active')).toHaveAttribute('data-marker-id', await readingLineMessage(page));

  // At the bottom the reading line sits in the last rows, not on the very last one.
  await transcript(page).evaluate(el => { el.scrollTop = el.scrollHeight; });
  const activeIndex = () => page.locator('.minimap-marker.active').evaluate(el => Array.from(document.querySelectorAll('.minimap-marker:not(.history)')).indexOf(el as Element));
  await expect.poll(activeIndex).toBeGreaterThanOrEqual(58);

  // Clicking a dash puts that row at the top of the viewport.
  await page.locator('[data-marker-id="history-71"]').click();
  await expect.poll(() => topMessage(page)).toBe('history-71');
  await expect(page.locator('.minimap-marker.active')).toHaveAttribute('data-marker-id', 'history-71');
  expect(errors).toEqual([]);
});

test('hovering a dash previews that row without leaving the rail', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await expect.poll(async () => (await railState(page)).markers).toBe(60);

  await expect(page.locator('.minimap-popover')).toHaveCount(0);
  // history-62 is a user turn, history-63 the assistant's answer to it.
  await page.locator('[data-marker-id="history-62"]').hover();
  const popover = page.locator('.minimap-popover');
  await expect(popover).toBeVisible();
  await expect(popover.locator('.minimap-popover-role')).toHaveText('你');
  await expect(popover.locator('.minimap-popover-text')).toContainText('演示历史问题 63');
  // The magnification is written imperatively, so it must be gone once the pointer leaves.
  await expect(page.locator('[data-marker-id="history-62"]')).toHaveCSS('--magnify', /.+/);
  await page.locator('[data-marker-id="history-63"]').hover();
  await expect(popover.locator('.minimap-popover-role')).toHaveText('声学助手');
  await expect(popover.locator('.minimap-popover-text')).toContainText('第 64 条夹具');
  await page.locator('.composer').hover();
  await expect(page.locator('.minimap-popover')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a grouped dash announces its range and its list navigates to one message', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await revealHistory(page);

  // The oldest dash stands for history-21 and history-22, neither of them mounted.
  const group = page.locator('.minimap-marker.group').first();
  await expect(group).toHaveAttribute('data-group-key', 'history-21');
  await expect(group).toHaveAttribute('data-group-range', '第 22–23 条消息');
  await expect(group).toHaveAttribute('aria-expanded', 'false');
  await group.hover();
  await expect(page.locator('.minimap-popover-role')).toContainText('第 22–23 条消息');

  // Activating a range asks which message the reader meant instead of guessing one.
  await group.click();
  const list = page.locator('.minimap-group-list');
  await expect(list).toBeVisible();
  await expect(list).toHaveAttribute('aria-label', '第 22–23 条消息 中的消息');
  await expect(list.locator('button')).toHaveCount(2);
  await expect(group).toHaveAttribute('aria-expanded', 'true');
  // Counted from the first message of the session, not from the start of this page.
  await expect(list.locator('[data-group-item="history-21"]')).toHaveText(/^第 22 条/);
  await expect(list.locator('[data-group-item="history-22"]')).toHaveCount(1);

  // The explicit ID goes to the transcript's single positioning entry: the logical range opens
  // and the row lands at the reading line even though it was never mounted.
  await list.locator('[data-group-item="history-21"]').click();
  await expect(list).toHaveCount(0);
  await expect.poll(() => topMessage(page)).toBe('history-21');
  expect(errors).toEqual([]);
});

test('the in-group list stays bounded and inside the track', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await openLongHistory(page);
  await revealHistory(page);
  // A short window leaves the track room for only a few dashes, so each covers many messages.
  await page.setViewportSize({width: 900, height: 320});
  await expect.poll(async () => (await railState(page)).dashes).toBeLessThanOrEqual(8);
  const dense = await railState(page);
  expect(Math.max(...dense.sizes)).toBeGreaterThan(8);
  expect(dense.outside).toBe(0);

  await page.locator('.minimap-marker.group').first().click();
  const list = page.locator('.minimap-group-list');
  await expect(list).toBeVisible();
  await expect(list.locator('[data-group-item]')).toHaveCount(8);
  const firstPage = await list.locator('[data-group-item]').evaluateAll(rows => rows.map(row => row.getAttribute('data-group-item')));
  await page.getByRole('button', {name: '组内下一页消息', exact: true}).click();
  await expect(list.locator('[data-group-item]')).toHaveCount(8);
  expect(await list.locator('[data-group-item]').evaluateAll(rows => rows.map(row => row.getAttribute('data-group-item')))).not.toEqual(firstPage);
  await expect(page.getByRole('button', {name: '组内上一页消息', exact: true})).toBeVisible();
  // The window is the one holding the message the reader was on, marked as such.
  await expect(list.locator('button[aria-current="true"]')).toHaveCount(1);
  expect(await list.evaluate(panel => {
    const box = panel.getBoundingClientRect();
    const track = document.querySelector('.minimap-rail')!.getBoundingClientRect();
    return Math.max(0, track.top - box.top, box.bottom - track.bottom);
  })).toBeLessThanOrEqual(1);

  await page.keyboard.press('Escape');
  await expect(list).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a history that fits needs no rail', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText: '演示旧记录'}).click();
  await expect(page.locator('.message')).toHaveCount(2);
  await expect(rail(page)).toHaveCount(0);
  expect(errors).toEqual([]);
});

for (const theme of ['light', 'dark'] as const) test(`message content stays clear of the minimap at every window size in ${theme}`, async ({page}, testInfo) => {
  await page.emulateMedia({colorScheme: theme, reducedMotion: 'reduce'});
  await openLongHistory(page);
  await page.getByRole('button', {name: '折叠会话侧栏'}).click();
  await page.getByRole('textbox', {name: '消息输入'}).fill([
    '## 计算依据',
    '- VOT：(0.598490 − 0.567990) × 1000 = 30.500 ms。',
    '- 爆破后稳定周期间隔：30.500 ms。',
    '  - 周期性不等于清晰元音起始。',
    '> 自动地标仍需结合闭塞段与语图复核。',
    '| 地标 | 时间 |', '| --- | --- |', '| 预浊起始 | 未确定 |',
    '```text', 'long_unbroken_token_'.repeat(20), '```',
  ].join('\n\n'));
  await page.getByRole('button', {name: '发送消息'}).click();
  await expect(page.locator('.message.user .prose ul').last()).toBeAttached();
  await expect(page.getByRole('button', {name: '发送消息'})).toBeAttached({timeout: 15000});

  for (const expanded of [false, true]) {
    if (expanded) await page.getByRole('button', {name: '展开会话侧栏'}).click();
    for (const width of [360, 660, 800, 1280, 1920]) {
      await page.setViewportSize({width, height: 760});
      for (const key of ['Home', 'End']) {
        await page.locator('.chat-width-handle-right').focus();
        await page.keyboard.press(key);
        await expect(rail(page)).toBeVisible();
        const bounds = await page.evaluate(() => {
          const rail = document.querySelector('.minimap-rail')!.getBoundingClientRect();
          const viewport = document.querySelector('.transcript')!;
          const pane = viewport.getBoundingClientRect();
          const band = document.querySelector('.chat-width-band')!.getBoundingClientRect();
          const content = document.querySelector('.transcript-content')!.getBoundingClientRect();
          const messages = Array.from(document.querySelectorAll('.message-content'), node => node.getBoundingClientRect());
          return {
            gap: Math.min(...messages.map(box => box.left - rail.right)),
            rightGap: Math.min(...messages.map(box => pane.right - box.right)),
            overflow: viewport.scrollWidth - viewport.clientWidth,
            centerOffset: (content.left + content.right - pane.left - pane.right) / 2,
            handleOffset: Math.abs(content.left - band.left) + Math.abs(content.right - band.right),
          };
        });
        const context = JSON.stringify({width, expanded, key, ...bounds});
        expect(bounds.gap, context).toBeGreaterThanOrEqual(8);
        expect(bounds.rightGap, context).toBeGreaterThanOrEqual(20);
        expect(bounds.overflow, context).toBe(0);
        expect(Math.abs(bounds.centerOffset), context).toBeLessThanOrEqual(4);
        expect(bounds.handleOffset, context).toBeLessThanOrEqual(8);
        if (width === 660 && !expanded && key === 'End') {
          await page.screenshot({path: testInfo.outputPath(`minimap-${theme}-660.png`)});
        }
      }
    }
  }
});
