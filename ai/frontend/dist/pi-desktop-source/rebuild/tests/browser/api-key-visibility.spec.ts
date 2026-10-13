import {expect,test} from '@playwright/test';
import {join} from 'node:path';

for (const viewport of [{width:900,height:640},{width:390,height:740}]) {
  test(`API key has one persistent inner toggle and aligned fields at ${viewport.width}px`,async({page})=>{
    await page.setViewportSize(viewport);
    await page.goto('/?demo=1');
    await page.getByRole('button',{name:'打开设置',exact:true}).click();
    const input=page.getByLabel('API Key',{exact:true});
    const toggle=page.getByRole('button',{name:/^(显示|隐藏) API Key$/});
    await expect(input).toHaveAttribute('type','password');
    await expect(toggle).toHaveCount(1);
    await expect(toggle).toBeVisible();

    const field=await input.boundingBox(),model=await page.getByLabel('模型名',{exact:true}).boundingBox();
    expect(Math.abs(field!.x-model!.x)).toBeLessThan(1);
    expect(Math.abs(field!.width-model!.width)).toBeLessThan(1);
    const button=await toggle.boundingBox();
    expect(button!.x).toBeGreaterThanOrEqual(field!.x);
    expect(button!.x+button!.width).toBeLessThanOrEqual(field!.x+field!.width);
    expect(button!.y).toBeGreaterThanOrEqual(field!.y);
    expect(button!.y+button!.height).toBeLessThanOrEqual(field!.y+field!.height);

    await input.pressSequentially('fixture-key-visibility');
    for(const type of ['text','password','text','password']) {
      await toggle.click();
      await expect(input).toHaveAttribute('type',type);
      await expect(input).toHaveValue('fixture-key-visibility');
      await expect(toggle).toBeVisible();
      await expect(toggle).toHaveAttribute('aria-pressed',String(type==='text'));
    }
    await page.getByLabel('模型名',{exact:true}).focus();
    await expect(toggle).toBeVisible();
    await input.fill('');
    await toggle.focus();
    await toggle.press('Enter');
    await expect(input).toHaveAttribute('type','text');
    await expect(toggle).toBeFocused();
    await toggle.press('Enter');
    await expect(input).toHaveAttribute('type','password');
    await expect(toggle).toBeVisible();
    if(process.env.PI_SCRATCH_DIR) await page.locator('.settings-main .card').first().screenshot({path:join(process.env.PI_SCRATCH_DIR,`api-key-visibility-${viewport.width}.png`)});
  });
}
