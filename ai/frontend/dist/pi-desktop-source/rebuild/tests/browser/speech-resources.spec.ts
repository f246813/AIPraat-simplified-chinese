import {expect, test} from '@playwright/test';
import { join } from 'node:path';
import { mfaFixture, readyStatus } from '../mfa-fixtures';
const screenshot = (name: string) => {if (!process.env.PI_SCRATCH_DIR) throw new Error('PI_SCRATCH_DIR is required for browser screenshots'); return join(process.env.PI_SCRATCH_DIR, name);};

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
    await page.screenshot({path:screenshot(`dictionary-models-${width}.png`)});
    await page.getByRole('button',{name:'MFA语音词典',exact:true}).click();
    await expect(page.getByRole('button',{name:'添加MFA语音词典'})).toBeVisible();
  }
});

test('shared resource pairing editor supports persistent import, discard, launcher checks and save without legacy activation', async ({page}) => {
  await page.addInitScript(({mfa,status}) => {
    (window as any).resourceCalls = [];
    (window as any).pywebview = {api:{rpc:async (method:string,params:any) => {
      (window as any).resourceCalls.push({method,params});
      if (method === 'mfa.choose') {mfa.resources[params.kind as 'g2p'].push({id:'new-g',name:'新增本地 G2P.zip',path:'C:/资源/新增本地 G2P.zip',exists:true,used_by:[]}); return mfa;}
      if (method === 'mfa.check') return status;
      if (method === 'mfa.choose_launcher') return {path:'C:/中文路径/conda.exe'};
      if (method === 'mfa.check_launcher') return {version:'3.3',help_order:'INPUT MODEL OUTPUT',status:'ok'};
      if (method === 'mfa.save') {mfa.presets = mfa.presets.map(p => p.id === params.preset.id ? {...params.preset,status} : p); mfa.launcher = params.launcher; if (params.make_default) mfa.default_preset = params.preset.id; return mfa;}
      if (method === 'mfa.remove') {mfa.resources.g2p = mfa.resources.g2p.filter(r => r.id !== params.id); return mfa;}
      return method === 'mfa.list' ? mfa : {dictionaries:[],theme:'system'};
    }}};
  },{mfa:mfaFixture,status:readyStatus});
  for (const width of [1040,600,375]) {
    await page.setViewportSize({width,height:850}); await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'}); await page.goto('/?window=dictionaries');
    await page.getByRole('button',{name:'纠音资源配套',exact:true}).click(); await page.getByRole('button',{name:'配置 英语（英式） 配套资源'}).click();
    let editor = page.getByRole('dialog',{name:'配置配套资源 · 英语（英式）'}); await expect(editor).toBeVisible();
    await editor.getByRole('button',{name:'添加本地G2P模型'}).click(); await expect(editor.getByRole('combobox',{name:'G2P 模型'})).toHaveValue('');
    await editor.getByRole('combobox',{name:'G2P 模型'}).selectOption('new-g'); await editor.getByRole('button',{name:'取消',exact:true}).click();
    await expect(editor.getByText(/已导入资源仍保留/)).toBeVisible(); await editor.getByRole('button',{name:'丢弃并关闭'}).click();
    await page.getByRole('button',{name:'MFA G2P 模型',exact:true}).click(); await expect(page.getByText('新增本地 G2P.zip',{exact:true})).toBeVisible();
    await page.screenshot({path:screenshot(`g2p-library-dark-${width}.png`)}); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button',{name:'移除 English G2P.zip 登记'}).click(); await expect(page.getByText(/受影响预设：英语（美式）/)).toBeVisible(); await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.getByRole('button',{name:'纠音资源配套',exact:true}).click(); await page.getByRole('button',{name:'配置 英语（英式） 配套资源'}).click();
    editor = page.getByRole('dialog',{name:'配置配套资源 · 英语（英式）'});
    await editor.getByRole('combobox',{name:'G2P 模型'}).selectOption('new-g'); await editor.getByRole('combobox',{name:'MFA 发音词典'}).selectOption('d'); await editor.getByRole('combobox',{name:'MFA 声学模型'}).selectOption('a');
    await editor.getByRole('checkbox',{name:/我确认/}).check();
    await editor.getByRole('button',{name:'选择Conda路径'}).click(); await expect(editor.getByRole('textbox',{name:'Conda 可执行文件路径'})).toHaveValue('C:/中文路径/conda.exe');
    await editor.getByRole('textbox',{name:'MFA 可执行文件路径'}).fill(''); await editor.getByRole('textbox',{name:'Conda 环境名'}).fill('mfa');
    await editor.getByRole('button',{name:'检查 MFA'}).click(); await expect(editor.getByText(/版本：3.3/)).toBeVisible();
    await editor.getByRole('button',{name:'检查兼容性'}).click(); await expect(editor.getByText(/静态检查不等于真实/)).toBeVisible();
    await page.screenshot({path:screenshot(`mfa-editor-checked-dark-${width}.png`)});
    await editor.getByRole('button',{name:'保存配套'}).click(); await expect(editor).toHaveCount(0);
    await page.getByRole('button',{name:'配置 英语（英式） 配套资源'}).click(); await expect(editor.getByRole('combobox',{name:'G2P 模型'})).toHaveValue('new-g'); await editor.getByRole('button',{name:'取消',exact:true}).click();
    const calls = await page.evaluate(() => (window as any).resourceCalls);
    expect(calls.some((c:any) => /^(dictionaries|acoustic_models)\.(select|choose)/.test(c.method))).toBe(false);
    expect(calls.filter((c:any) => c.method === 'mfa.save')).toHaveLength(1);
  }
});
