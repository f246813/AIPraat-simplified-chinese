import { expect, test } from '@playwright/test';
import { join } from 'node:path';

test('streaming steps share one closed disclosure and preserve manual expansion', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('/?demo=1&compactProcess=1');
  await page.getByRole('textbox', {name:'消息输入'}).fill('测试紧凑对话');
  await page.getByRole('button', {name:'发送消息'}).click();
  const disclosure = page.locator('.process-details');
  await expect(disclosure).toHaveCount(1);
  await expect(disclosure).not.toHaveAttribute('open', '');
  await expect(page.locator('.avatar,.message-label')).toHaveCount(0);
  await expect(page.locator('.message.user .prose')).toHaveCSS('font-size', '13px');
  await expect(page.locator('.message.user .prose')).toHaveCSS('border-top-width', '0px');
  await expect(page.locator('.message.assistant > .message-content > .prose')).toHaveCSS('font-size', '14px');
  await expect.poll(() => disclosure.locator('.activity').count()).toBeGreaterThan(3);
  await expect(disclosure.locator('.activity').first()).not.toBeVisible();
  await disclosure.locator(':scope > summary').focus();
  await page.keyboard.press('Enter');
  await expect(disclosure).toHaveAttribute('open', '');
  const count = await disclosure.locator('.activity').count();
  await expect.poll(() => disclosure.locator('.activity').count()).toBeGreaterThan(count);
  await expect(disclosure).toHaveAttribute('open', '');
  await page.keyboard.press('Enter');
  await expect(disclosure).not.toHaveAttribute('open', '');
  await expect(disclosure.locator(':scope > summary')).toContainText('执行过程',{timeout:15000});
  expect(await disclosure.locator('.activity').count()).toBeGreaterThan(20);
  await expect(disclosure).not.toHaveAttribute('open', '');
  await expect(disclosure.locator(':scope > summary')).toContainText('执行过程');
  await expect(disclosure.locator(':scope > summary')).toContainText('个步骤');
  await expect(page.locator('.message.assistant .prose').last()).toContainText('所有数字');
  if (process.env.PI_SCRATCH_DIR) await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,'compact-transcript.png')});
  expect(errors).toEqual([]);
});

test('search reveals nested tool evidence and the process closes without losing the answer', async ({page}) => {
  await page.goto('/?demo=1&workflowResults=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-1"] .session-select').click();
  const disclosure = page.locator('.process-details');
  await expect(page.getByRole('region',{name:'已保存的工具结果'})).toBeVisible();
  await expect(disclosure).not.toHaveAttribute('open','');
  await page.getByRole('button',{name:'在会话中查找（Ctrl / Cmd + K）'}).click();
  await page.getByRole('searchbox',{name:'在会话中查找'}).fill('离线工具结果夹具');
  await expect(page.locator('.chat-search-count')).toHaveText('1/1');
  await page.getByRole('searchbox',{name:'在会话中查找'}).press('Enter');
  await expect(disclosure).toHaveAttribute('open','');
  await expect(page.locator('.process-results .json')).toBeVisible();
  await page.getByRole('button',{name:'关闭查找',exact:true}).click();
  await disclosure.locator(':scope > summary').click();
  await expect(page.getByRole('region',{name:'已保存的工具结果'})).toContainText('1.250000 秒');
  await expect(page.getByText('已取消后续解释。离线展示夹具。')).toBeVisible();
});

for (const theme of ['light','dark'] as const) test(`compact messages fit narrow ${theme} windows`, async ({page}) => {
  await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
  await page.setViewportSize({width:375,height:760});
  await page.goto('/?demo=1&compactProcess=1');
  await page.getByRole('textbox',{name:'消息输入'}).fill('## 一条较长的用户消息，检查字号、换行和边框。'.repeat(6));
  await page.getByRole('button',{name:'发送消息'}).click();
  await expect(page.locator('.process-details')).toHaveCount(1);
  await expect(page.locator('.message.user .prose')).toHaveCSS('border-top-width','0px');
  expect(await page.locator('.transcript').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.getByRole('button',{name:'暂停当前任务'}).click();
  await expect(page.locator('.process-details > summary')).toContainText('已取消');
  await expect(page.locator('.process-details')).not.toHaveAttribute('open','');
  if (process.env.PI_SCRATCH_DIR) await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,`compact-${theme}-375.png`)});
});
