import {expect, test} from '@playwright/test';

test('send button becomes the pause action and restores after cancellation or completion', async ({page}) => {
  await page.goto('/?demo=1');
  const input = page.getByRole('textbox', {name:'消息输入'});
  const action = page.locator('.composer-actions .send-button');
  await expect(action).toHaveCount(1);
  await expect(action).toHaveAccessibleName('发送消息');
  await input.fill('测试暂停');
  await action.click();
  await expect(action).toHaveAccessibleName('暂停当前任务');
  await expect(action).toBeEnabled();
  await expect(action.locator('.send-loading-square')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(action.locator('.send-loading-square')).toHaveCSS('animation-name', 'none');
  await expect(page.locator('.stop-button')).toHaveCount(0);
  await input.fill('保留下一条草稿');
  await action.click();
  await expect(action).toHaveAccessibleName('发送消息');
  await expect(page.locator('.message.assistant .badge')).toContainText('已取消');
  await expect(input).toHaveText('保留下一条草稿');
  await expect(page.locator('.message.user')).toHaveCount(1);
  await page.emulateMedia({reducedMotion:'reduce'});
  await action.click();
  await expect(action).toHaveAccessibleName('暂停当前任务');
  await expect(action.locator('.send-loading-square')).toHaveCSS('animation-name', 'none');
  await expect(action).toHaveAccessibleName('发送消息', {timeout:15000});
  await expect(action.locator('.send-loading-square')).toHaveCount(0);
  await expect(page.locator('.message.user')).toHaveCount(2);
});
