import {test,expect} from '@playwright/test';
test('Pi extracted session menu: toggle, keyboard, outside, scroll, right click, pin and legacy guards',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  const trigger=page.getByRole('button',{name:'会话操作 新的分析',exact:true}); const menu=page.getByRole('menu');
  await trigger.click(); await expect(menu).toBeVisible();await expect(page.getByRole('menuitem',{name:'重命名',exact:true})).toBeFocused();
  await page.keyboard.press('ArrowDown');await expect(page.getByRole('menuitem',{name:'置顶',exact:true})).toBeFocused();
  await page.keyboard.press('End');await expect(page.getByRole('menuitem',{name:'删除',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(trigger).toBeFocused();
  await trigger.click();await trigger.click();await expect(menu).toHaveCount(0);
  await trigger.click();await page.locator('.chat-header h1').click();await expect(menu).toHaveCount(0);
  await trigger.click();await page.locator('.session-list').dispatchEvent('scroll');await expect(menu).toHaveCount(0);
  await page.locator('[data-session-id="demo-history"]').click({button:'right'});await expect(menu).toHaveCount(1);
  await page.getByRole('menuitem',{name:'置顶',exact:true}).click();await expect(page.locator('.session-row').first()).toHaveAttribute('data-session-id','demo-history');
  await expect(page.locator('.session-row').first()).toHaveAttribute('data-pinned','true');await expect(page.locator('.chat-header h1')).toContainText('新会话');
  await page.getByRole('button',{name:'会话操作 长历史与渲染夹具',exact:true}).click();await page.getByRole('menuitem',{name:'取消置顶',exact:true}).click();await expect(page.locator('[data-session-id="demo-history"]')).toHaveAttribute('data-pinned','false');
  await page.getByRole('button',{name:'会话操作 演示旧记录（只读）',exact:true}).click();for(const item of ['重命名','置顶'])await expect(page.getByRole('menuitem',{name:item,exact:true})).toBeDisabled();
  await expect(page.getByRole('menuitem',{name:'删除',exact:true})).toBeEnabled();
  await page.getByRole('menuitem',{name:'删除',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'确认删除？',exact:true})).toBeFocused();
  await expect(page.getByRole('menuitem',{name:'确认删除？',exact:true}).locator('svg.lucide-x')).toHaveCount(1);
  await expect(page.getByRole('menuitem',{name:'确认删除？',exact:true})).toHaveCSS('color','rgb(182, 65, 66)');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toBeVisible();
  await page.locator('[data-session-id="legacy:fixture"]').click({button:'right'});
  await page.getByRole('menuitem',{name:'删除',exact:true}).click();
  await page.getByRole('menuitem',{name:'确认删除？',exact:true}).click();
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toHaveCount(0);
});
test('running session cannot delete but may pin; narrow viewport and exact 6px scrollbar',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('textbox',{name:'消息输入'}).fill('后台夹具');await page.getByRole('button',{name:'发送消息'}).click();
  await page.getByRole('button',{name:'展开会话侧栏'}).click();await page.getByRole('button',{name:'会话操作 新会话',exact:true}).click();
  // The reply adjusts the conversation's own scroll while it streams. That region does not hold this
  // menu's anchor, so its scrolls must not dismiss the menu the reader is still using.
  await page.locator('.transcript').dispatchEvent('scroll');await expect(page.getByRole('menu')).toBeVisible();
  await expect(page.getByRole('menuitem',{name:'删除',exact:true})).toBeDisabled();await expect(page.getByRole('menuitem',{name:'置顶',exact:true})).toBeEnabled();
  await page.getByRole('menuitem',{name:'置顶',exact:true}).click();await expect(page.locator('[data-session-id="demo-new-1"]')).toHaveAttribute('data-pinned','true');
  await page.getByRole('button',{name:'暂停当前任务'}).click();await page.setViewportSize({width:390,height:700});
  await page.locator('[data-session-id="demo-history"]').click({button:'right',position:{x:200,y:15}});
  const box=await page.getByRole('menu').boundingBox();expect(box!.x).toBeGreaterThanOrEqual(8);expect(box!.x+box!.width).toBeLessThanOrEqual(382);expect(box!.y+box!.height).toBeLessThanOrEqual(692);
  await page.keyboard.press('Escape');await page.locator('[data-session-id="demo-history"] .session-select').click();
  const transcript=page.locator('.transcript');expect(await transcript.evaluate(el=>getComputedStyle(el,'::-webkit-scrollbar').width)).toBe('6px');
  expect(await transcript.evaluate(el=>getComputedStyle(el).scrollbarWidth)).toBe('auto');
  await transcript.evaluate(el=>el.dispatchEvent(new Event('scroll')));await expect(transcript).toHaveAttribute('data-scrolling','');
  await page.waitForTimeout(400);await expect(transcript).not.toHaveAttribute('data-scrolling','');
});
