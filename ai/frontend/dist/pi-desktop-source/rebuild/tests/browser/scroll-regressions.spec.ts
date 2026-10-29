import {expect, test, type Page} from '@playwright/test';

async function openHistory(page: Page) {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
}
const fixture = (page: Page, index: number, offset: number) => page.evaluate(({index, offset}) => {
  (window as unknown as {transcriptFixture: {go: (index: number, offset: number) => void}}).transcriptFixture.go(index, offset);
}, {index, offset});
const offsetOf = (page: Page, index: number) => page.locator(`[data-row-id="row-${index}"]`).evaluate(row =>
  row.getBoundingClientRect().top - document.querySelector('.fixture-viewport')!.getBoundingClientRect().top);

test('an estimated offscreen target settles at its measured offset, including withheld history', async ({page}) => {
  await page.goto('/tests/browser/fixtures/virtual-transcript.html');
  await expect(page.locator('[data-row-id]')).not.toHaveCount(0);
  for (const [index, offset] of [[1501, -13.5], [3, 18], [700, -32.25]]) {
    await fixture(page, index, offset);
    await expect(page.locator(`[data-row-id="row-${index}"]`)).toBeAttached();
    await expect.poll(async () => Math.abs(await offsetOf(page, index) - offset)).toBeLessThan(1);
    await page.waitForTimeout(250);
    expect(Math.abs(await offsetOf(page, index) - offset)).toBeLessThan(1);
    expect(await page.locator('[data-row-id]').count()).toBeLessThan(40);
  }
});

test('the sized container includes the entire last row with a nonzero list origin', async ({page}) => {
  await page.goto('/tests/browser/fixtures/virtual-transcript.html?count=90');
  await expect(page.locator('[data-row-id]')).not.toHaveCount(0);
  await page.evaluate(() => (window as unknown as {transcriptFixture: {latest: () => void}}).transcriptFixture.latest());
  const last = page.locator('[data-row-id="row-89"]');
  await expect(last).toBeAttached();
  await expect.poll(() => last.evaluate(row => row.getBoundingClientRect().bottom - row.parentElement!.getBoundingClientRect().bottom)).toBeLessThanOrEqual(1);
});

test('search aligns a hit deep inside a tall message', async ({page}) => {
  await openHistory(page);
  await page.locator('[data-message-id="diagram-fixture"] .message-content').evaluate(el => {
    const block = document.createElement('div');
    block.textContent = 'Large rendered result';
    block.style.height = '2200px';
    const needle = document.createElement('p'); needle.textContent = 'DEEP_RENDERED_NEEDLE';
    el.append(block, needle);
  });
  await page.waitForTimeout(250);
  await page.locator('.transcript').evaluate(el => {
    const row = el.querySelector('[data-message-id="diagram-fixture"]')!;
    el.scrollTop += row.getBoundingClientRect().top - el.getBoundingClientRect().top;
  });
  await page.keyboard.press('Control+k');
  await page.getByRole('searchbox').fill('DEEP_RENDERED_NEEDLE');
  await expect(page.locator('.chat-search-count')).toHaveText('1/1');
  await page.getByRole('button', {name: '下一个匹配', exact: true}).click();
  await expect.poll(() => page.evaluate(() => {
    const hit = (CSS.highlights.get('transcript-search-active') as Set<Range> | undefined)?.values().next().value;
    if (!hit) return false;
    const rect = hit.getBoundingClientRect();
    const view = document.querySelector('.transcript')!.getBoundingClientRect();
    return rect.top >= view.top && rect.bottom <= view.bottom;
  })).toBe(true);
});

test('process and nested tool disclosures survive message recycling', async ({page}) => {
  await openHistory(page);
  const process = page.locator('[data-message-id="diagram-fixture"] .process-details');
  await process.locator(':scope > summary').click();
  const tool = process.locator('[data-activity-id="fixture-tool"]');
  await tool.locator(':scope > summary').click();
  await expect(tool).toHaveAttribute('open', '');
  await page.locator('.transcript').focus();
  for (let round = 0; round < 5; round++) {
    await page.locator('.transcript').evaluate(el => { el.scrollTop = 0; });
    await page.waitForTimeout(150);
  }
  await expect(process).not.toBeAttached();
  await page.getByRole('button', {name: '回到最新消息', exact: true}).click();
  await expect(process).toHaveAttribute('open', '');
  await expect(tool).toHaveAttribute('open', '');
});

test('appending output retains the beginning of the opened history and its reading anchor', async ({page}) => {
  await page.goto('/tests/browser/fixtures/chat-transcript.html');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  await page.locator('.transcript').evaluate(el => { el.scrollTop = 250; });
  await expect(page.locator('[data-message-id="history-61"]')).toBeAttached();
  const before = await page.locator('[data-message-id="history-61"]').evaluate(el => el.getBoundingClientRect().top);
  await page.evaluate(() => (window as unknown as {chatTranscriptFixture: {append: () => Promise<void>}}).chatTranscriptFixture.append());
  await expect(page.locator('.history-reveal button')).toContainText('显示更早的 61 条消息');
  await expect.poll(async () => Math.abs(await page.locator('[data-message-id="history-61"]').evaluate(el => el.getBoundingClientRect().top) - before)).toBeLessThanOrEqual(2);
  await expect(page.getByRole('button', {name: '回到最新消息', exact: true})).toBeVisible();
});

test('a width change preserves the measured reading position', async ({page}) => {
  await page.goto('/tests/browser/fixtures/virtual-transcript.html');
  await expect(page.locator('[data-row-id]')).not.toHaveCount(0);
  await fixture(page, 803, -12);
  await expect.poll(async () => Math.abs(await offsetOf(page, 803) + 12)).toBeLessThan(1);
  await page.evaluate(() => (window as unknown as {transcriptFixture: {width: (width: number) => void}}).transcriptFixture.width(320));
  await expect.poll(async () => Math.abs(await offsetOf(page, 803) + 12)).toBeLessThan(2);
});

test('twenty thousand messages still mount a bounded slice after distant jumps', async ({page}) => {
  await page.goto('/tests/browser/fixtures/virtual-transcript.html?count=20000');
  await expect(page.locator('[data-row-id]')).not.toHaveCount(0);
  for (const index of [19003, 63, 11230]) {
    await fixture(page, index, -16);
    await expect.poll(async () => Math.abs(await offsetOf(page, index) + 16)).toBeLessThan(1);
    expect(await page.locator('[data-row-id]').count()).toBeLessThan(40);
  }
});

test('a selection beyond the pin budget retains its anchor and stops at the budget boundary', async ({page}) => {
  await openHistory(page);
  await page.locator('.transcript').evaluate(el => { el.scrollTop = 250; });
  const first = page.locator('[data-message-id="history-61"]');
  await expect(first).toBeAttached();
  await first.evaluate(el => {
    const text = document.createTreeWalker(el.querySelector('.message-content')!, NodeFilter.SHOW_TEXT).nextNode()!;
    window.getSelection()!.setBaseAndExtent(text, 0, text, 1);
  });
  await page.waitForTimeout(50);
  await page.locator('.transcript').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  await expect(first).toBeAttached();
  await page.locator('[data-message-id="diagram-fixture"]').evaluate(el => {
    const text = document.createTreeWalker(el.querySelector('.message-content')!, NodeFilter.SHOW_TEXT).nextNode()!;
    window.getSelection()!.extend(text, 1);
  });
  await expect.poll(() => page.evaluate(() => {
    const selection = window.getSelection()!;
    const id = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest('[data-message-id]')?.getAttribute('data-message-id');
    return {anchor: id(selection.anchorNode), focus: id(selection.focusNode), collapsed: selection.isCollapsed};
  })).toEqual({anchor: 'history-61', focus: 'history-100', collapsed: false});
});
