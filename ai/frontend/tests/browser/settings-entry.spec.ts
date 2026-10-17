import {expect,test} from '@playwright/test';

test('collapsed gear opens Model; expanded gear keeps Session and Storage',async({page})=>{
  await page.goto('/?demo=1');
  await expect(page.locator('.chat-header')).toBeVisible();
  await expect(page.getByRole('button',{name:'模型设置',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'打开设置',exact:true}).click();
  await expect(page.getByRole('heading',{name:'模型',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'云端 API',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'返回聊天',exact:true}).click();
  await page.getByRole('button',{name:'展开会话侧栏',exact:true}).click();
  await page.getByRole('button',{name:'打开设置',exact:true}).click();
  await expect(page.getByRole('heading',{name:'会话状态',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'模型',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'返回聊天',exact:true}).click();
  await page.getByRole('button',{name:'折叠会话侧栏',exact:true}).click();
  await page.getByRole('button',{name:'打开设置',exact:true}).click();
  await expect(page.getByRole('heading',{name:'模型',exact:true})).toBeVisible();
});
