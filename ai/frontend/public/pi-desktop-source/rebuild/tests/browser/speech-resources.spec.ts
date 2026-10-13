import {expect, test} from '@playwright/test';

test('dictionary and model navigation reuse the assistant layout at desktop and minimum sizes', async ({page}) => {
  await page.addInitScript(() => {
    const dictionary = {name:'英语.dict',path:'C:/MFA/dictionary/英语.dict',exists:true,active:true};
    const model = {name:'英语声学.zip',path:'C:/MFA/acoustic/英语声学.zip',exists:true,active:true};
    const wav = {name:'中文Wav2Vec2模型',path:'C:/Wav2Vec2/中文模型',exists:true,active:true};
    (window as any).pywebview = {api:{rpc:async (method:string) => method.startsWith('wav2vec2_models.') ? {models:[wav],theme:'light'} : method.startsWith('acoustic_models.') ? {models:[model],theme:'light'} : {dictionaries:[dictionary],theme:'light'}}};
  });
  for (const width of [780,620]) {
    await page.setViewportSize({width,height:width===780?480:360});
    await page.goto('/?window=dictionaries');
    await expect(page.getByRole('button',{name:'添加MFA语音词典'})).toBeVisible();
    await page.getByRole('button',{name:'MFA声学模型',exact:true}).click();
    await expect(page.getByRole('button',{name:'添加MFA声学模型'})).toBeVisible();
    const row = page.getByRole('button',{name:/英语声学.zip/});
    await expect(row).toHaveClass(/is-active/);
    await row.click({button:'right'});
    await expect(page.getByRole('menuitem',{name:'选用该模型'})).toBeDisabled();
    await page.getByRole('menuitem',{name:'删除'}).click();
    await expect(page.getByRole('dialog',{name:'删除MFA声学模型'})).toBeVisible();
    await page.getByRole('button',{name:'取消',exact:true}).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    const sidebar = await page.locator('.dictionary-nav').boundingBox();
    const content = await page.locator('.dictionary-window').boundingBox();
    expect(sidebar!.x+sidebar!.width).toBeLessThanOrEqual(content!.x);
    const divider = (await page.getByRole('separator').boundingBox())!;
    const mfa = (await page.getByRole('button',{name:'MFA声学模型',exact:true}).boundingBox())!;
    const wav = (await page.getByRole('button',{name:'Wav2Vec2模型',exact:true}).boundingBox())!;
    expect(divider.x).toBeGreaterThan(sidebar!.x);
    expect(divider.x+divider.width).toBeLessThan(sidebar!.x+sidebar!.width);
    expect(divider.height).toBe(1);
    expect(divider.y).toBeGreaterThan(mfa.y+mfa.height);
    expect(divider.y+divider.height).toBeLessThan(wav.y);
    await page.getByRole('button',{name:'Wav2Vec2模型',exact:true}).click();
    await expect(page.getByRole('heading',{name:'管理Wav2Vec2声学模型'})).toBeVisible();
    await expect(page.getByRole('button',{name:'添加Wav2Vec2声学模型'})).toBeVisible();
    await expect(page.getByRole('button',{name:/中文Wav2Vec2模型/})).toHaveClass(/is-active/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`../../test-records/installer/dictionary-models-${width}.png`});
    await page.getByRole('button',{name:'MFA语音词典',exact:true}).click();
    await expect(page.getByRole('button',{name:'添加MFA语音词典'})).toBeVisible();
  }
});
