import {expect, test} from '@playwright/test';

test('closed diagrams coexist with footnotes and quotes, and code copy excludes Markdown', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {value: {writeText: async (text: string) => {
      (window as Window & {copiedCode?: string}).copiedCode = text;
    }}});
  });
  await page.goto('/tests/browser/fixtures/markdown.html');
  await expect(page.getByRole('img', {name: 'Mermaid 图表'})).toHaveCount(2);
  await expect(page.locator('.diagram svg')).toHaveCount(2);
  await page.getByRole('button', {name: '复制代码', exact: true}).click();
  expect(await page.evaluate(() => (window as Window & {copiedCode?: string}).copiedCode)).toBe('print("ok")\n');
  expect(errors).toEqual([]);
});

test('an oversized receiving fence stays complete as plain text and finalizes on completion', async ({page}) => {
  await page.goto('/tests/browser/fixtures/markdown.html?case=long');
  const expected = '中文😀'.repeat(10000);
  expect(await page.locator('.markdown-plain-fallback pre').textContent()).toBe('```text\n' + expected);
  await expect(page.locator('pre code')).toHaveCount(0);
  await page.getByRole('button', {name: '完成接收'}).click();
  await expect(page.locator('.markdown-plain-fallback')).toHaveCount(0);
  expect(await page.locator('pre code').textContent()).toBe(expected + '\n');
});
