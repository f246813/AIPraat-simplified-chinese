import {expect, test} from '@playwright/test';

/**
 * Search work is bounded by the mount, not by the history. The structural claim is that a pass never
 * walks the rendered transcript as a whole: every walk is rooted at one `[data-message-id]` row, and
 * an unchanged row is reused instead of re-matched, so moving between hits costs scans only for the
 * rows that newly mounted or whose text really changed.
 */
test('navigating search hits reuses per-row matches instead of rescanning the transcript', async ({page}) => {
  await page.goto('/?demo=1');
  await page.getByRole('button', {name: '展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  await page.keyboard.press('Control+k');
  await page.getByRole('searchbox', {name: '在会话中查找'}).fill('夹具');
  await expect(page.locator('.chat-search-count')).toHaveText(/^\d+\/\d+$/);

  await page.evaluate(() => {
    const original = document.createTreeWalker.bind(document);
    const counts = {wholeTranscript: 0, byRow: {} as Record<string, number>};
    (window as any).searchScans = counts;
    document.createTreeWalker = ((...args: Parameters<typeof original>) => {
      const id = (args[0] as unknown as HTMLElement)?.dataset?.messageId;
      if (id) counts.byRow[id] = (counts.byRow[id] ?? 0) + 1;
      else counts.wholeTranscript++;
      return original(...args);
    }) as typeof document.createTreeWalker;
  });

  for (let i = 0; i < 5; i++) await page.getByRole('button', {name: '下一个匹配', exact: true}).click();
  await expect.poll(() => page.locator('.chat-search-count').textContent()).toMatch(/^\d+\/\d+$/);

  const scans = await page.evaluate(() => (window as any).searchScans as {wholeTranscript: number; byRow: Record<string, number>});
  const total = Object.values(scans.byRow).reduce((sum, count) => sum + count, 0);
  expect(scans.wholeTranscript).toBe(0);
  // A row is re-matched only when its own text changed (opening a disclosure, streaming a reply) or
  // when it mounted again — never once per frame of the move between hits.
  expect(Math.max(...Object.values(scans.byRow))).toBeLessThanOrEqual(6);
  expect(total).toBeLessThanOrEqual(30);
});
