import {expect, test} from '@playwright/test';

test('a reply delivered with task completion still reveals progressively, while reopening history is immediate', async ({page}) => {
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.goto('/?demo=1&streamDelivery=complete');
  await page.getByRole('textbox', {name:'消息输入'}).fill('测试一次性交付的回复');
  await page.getByRole('button', {name:'发送消息'}).click();
  const samples = await page.evaluate(() => new Promise<number[]>(resolve => {
    const lengths: number[] = []; const start = performance.now();
    const sample = () => {
      const text = document.querySelector('.message.assistant > .message-content > .prose')?.textContent || '';
      if (lengths.at(-1) !== text.length) lengths.push(text.length);
      if (performance.now() - start < 2200) requestAnimationFrame(sample); else resolve(lengths);
    };
    sample();
  }));
  await test.info().attach('visible-text-lengths', {body:JSON.stringify(samples), contentType:'application/json'});
  expect(samples.length).toBeGreaterThan(10);
  expect(samples[0]).toBeLessThan(samples.at(-1)! / 2);
  await expect(page.locator('.message.assistant > .message-content > .prose')).toContainText('所有数字都不是专业测量结果');
  await expect(page.getByRole('button', {name:'发送消息'})).toBeVisible();
  await expect(page.getByRole('button', {name:'暂停当前任务'})).toHaveCount(0);
  await page.getByRole('button', {name:'展开会话侧栏'}).click();
  const activeId = await page.locator('.session-select[aria-current=page]').locator('..').getAttribute('data-session-id');
  await page.locator('[data-session-id="legacy:fixture"] .session-select').click();
  await page.locator(`[data-session-id="${activeId}"] .session-select`).click();
  await expect(page.locator('.message.assistant > .message-content > .prose')).toContainText('所有数字都不是专业测量结果');
});

test('ordinary streamed chunks remain progressive and cancellation ends cleanly', async ({page}) => {
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.goto('/?demo=1');
  await page.getByRole('textbox', {name:'消息输入'}).fill('分批接收文字');
  await page.getByRole('button', {name:'发送消息'}).click();
  const prose = page.locator('.message.assistant > .message-content > .prose');
  await expect(prose).toContainText('浏览器');
  await page.getByRole('button', {name:'暂停当前任务'}).click();
  await expect(page.locator('.message.assistant .badge')).toContainText('已取消');
  const stopped = await prose.textContent();
  await page.waitForTimeout(350);
  expect(await prose.textContent()).toBe(stopped);
});

test('the smooth setting and reduced-motion preference bypass text pacing', async ({page}) => {
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.goto('/?demo=1&streamDelivery=complete');
  await page.getByRole('button', {name:'打开设置', exact:true}).click();
  await page.getByRole('navigation', {name:'设置分类'}).getByRole('button', {name:'外观与交互', exact:true}).click();
  await page.getByRole('checkbox', {name:'平滑流式文字', exact:true}).uncheck();
  await page.getByRole('button', {name:'保存更改', exact:true}).click();
  await page.getByRole('button', {name:'返回聊天', exact:true}).click();
  await page.getByRole('textbox', {name:'消息输入'}).fill('关闭平滑显示');
  await page.getByRole('button', {name:'发送消息'}).click();
  await expect(page.locator('.message.assistant > .message-content > .prose')).toContainText('所有数字都不是专业测量结果');
  await page.goto('/?demo=1&streamDelivery=complete');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.getByRole('textbox', {name:'消息输入'}).fill('系统减少动态效果');
  await page.getByRole('button', {name:'发送消息'}).click();
  await expect(page.locator('.message.assistant > .message-content > .prose')).toContainText('所有数字都不是专业测量结果');
});
