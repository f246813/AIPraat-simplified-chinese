import {expect, test} from '@playwright/test';
import { join } from 'node:path';
import { mfaFixture, generationFixture } from '../mfa-fixtures';
const screenshot = (name: string) => {if (!process.env.PI_SCRATCH_DIR) throw new Error('PI_SCRATCH_DIR is required for browser screenshots'); return join(process.env.PI_SCRATCH_DIR, name);};

test.beforeEach(async ({page}) => {
  await page.addInitScript(({mfa,generation}) => {
    (window as any).tutorCalls = [];
    (window as any).pywebview = {api:{rpc:async (method: string, params: unknown) => {
      (window as any).tutorCalls.push({method,params});
      if (method === 'tutor.generate_phonemes') return {...generation,transcript:(params as any).transcript,words:[{...generation.words[0],candidates:[...generation.words[0].candidates,{raw:['HH','EH','L','OW'],ipa:['h','ɛ','l','oʊ'],source:'词典'}]}]};
      if (method === 'mfa.list' || method === 'mfa.choose') return mfa;
      if (method === 'mfa.save') {const p = (params as any).preset; mfa.presets = mfa.presets.map(value => value.id === p.id ? p : value); mfa.launcher = (params as any).launcher; return mfa;}
      if (method === 'mfa.check') return (params as any).preset.status;
      if (method === 'mfa.choose_launcher') return {path:'C:/MFA/mfa.exe'};
      if (method === 'mfa.check_launcher') return {version:'3.3',help_order:'INPUT MODEL OUTPUT',status:'ok'};
      return method === 'tutor.bootstrap' ? {
        sounds:[{id:'4',name:'标准音_hello'},{id:'9',name:'学习者_hello'}],
        profiles:[{id:'en-US-general-v1',name:'英语（美式）',language:'en'},{id:'ja-JP-standard-v1',name:'日语（标准语）',language:'ja'}],
        model:{source:'本地模型',name:'local-fixture'},theme:'system',mfa,
      } : {accepted:true};
    }}};
  }, {mfa:mfaFixture,generation:generationFixture});
});

test('desktop and small windows retain visible actions and scroll advanced fields without overlap', async ({page}) => {
  for (const [width,height] of [[1040,860],[600,540],[375,812]]) {
    await page.setViewportSize({width,height}); await page.goto('/?window=pronunciation');
    await expect(page.getByText(/当前模型：本地模型 · local-fixture/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const action = page.getByRole('button',{name:'开始分析'});
    const bounds = (await action.boundingBox())!;
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(height);
    await page.getByText('高级设置').click();
    await page.getByRole('checkbox',{name:/AI 视觉解释/}).check();
    await expect(page.getByRole('spinbutton',{name:/声学对比阈值/})).toBeEnabled();
    await page.screenshot({path:screenshot(`pronunciation-manual-${width}.png`)});
    await page.getByRole('button',{name:'取消'}).click();
    expect(await page.evaluate(() => (window as any).tutorCalls.at(-1).method)).toBe('tutor.cancel');
  }
});

test('validates IPA, selects Japanese rules, and submits only one analysis', async ({page}) => {
  await page.goto('/?window=pronunciation');
  await page.getByRole('textbox',{name:/目标音位/}).fill(' ,； ');
  await page.getByRole('button',{name:'开始分析'}).click();
  await expect(page.getByRole('alert')).toHaveText('请输入至少一个 IPA 音位。');
  expect(await page.evaluate(() => (window as any).tutorCalls.length)).toBe(1);
  await page.getByRole('textbox',{name:/目标音位/}).fill('ɯ̥ aː N');
  await page.getByRole('radio',{name:/音位判据/}).check();
  await page.getByRole('combobox',{name:'判据预设'}).selectOption('ja-JP-standard-v1');
  await expect(page.getByRole('textbox',{name:/目标语言/})).toHaveValue('ja');
  await page.getByText('高级设置').click();
  await expect(page.getByRole('spinbutton',{name:/声学对比阈值/})).toBeDisabled();
  await page.getByRole('button',{name:'开始分析'}).click();
  const submitted = await page.evaluate(() => (window as any).tutorCalls.filter((call: any) => call.method === 'tutor.submit'));
  expect(submitted).toHaveLength(1);
  expect(submitted[0].params).toMatchObject({reference:'4',learner:'9',profile_id:'ja-JP-standard-v1',language:'ja',phonemes:'ɯ̥ aː N'});
});

test('dark theme and keyboard focus render in the actual frontend', async ({page}) => {
  await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
  await page.goto('/?window=pronunciation');
  await expect(page.getByRole('button',{name:'开始分析'})).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.getByRole('combobox',{name:'标准音范本'}).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('combobox',{name:'学习者录音'})).toBeFocused();
  await page.screenshot({path:screenshot('pronunciation-manual-dark.png')});
});

test('G2P candidate confirmation, generated submission and resource editor stay usable at 1040/600/375 widths', async ({page}) => {
  for (const [width,height] of [[1040,860],[600,700],[375,812]]) {
    await page.setViewportSize({width,height}); await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'}); await page.goto('/?window=pronunciation');
    const target = page.getByRole('combobox',{name:'目标语言与读音'}); await expect(target).toHaveValue('');
    await expect(page.getByRole('textbox',{name:/目标音位/})).toHaveValue('');
    await target.selectOption('en-GB'); await expect(page.getByRole('radio',{name:/音位判据/})).toBeDisabled();
    await page.getByRole('textbox',{name:/参考文本/}).fill('hello hello'); await expect(page.getByRole('button',{name:'自动生成音位'})).toBeDisabled();
    await target.selectOption('en-US'); await page.getByRole('button',{name:'自动生成音位'}).click();
    await expect(page.getByRole('button',{name:'开始分析'})).toBeDisabled(); await expect(page.getByRole('textbox',{name:/目标音位/})).toHaveValue('');
    await page.getByRole('combobox',{name:'hello 的读音'}).selectOption('1'); await page.getByRole('button',{name:'确认并应用读音'}).click();
    await expect(page.getByRole('textbox',{name:/目标音位/})).toHaveValue('h ɛ l oʊ h ɛ l oʊ');
    await expect(page.getByRole('button',{name:'开始分析'})).toBeEnabled();
    await page.getByRole('radio',{name:/音位判据/}).check(); await expect(page.getByRole('combobox',{name:'判据预设'})).toHaveCount(0);
    await page.screenshot({path:screenshot(`pronunciation-mfa-dark-${width}.png`)});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const action = (await page.getByRole('button',{name:'开始分析'}).boundingBox())!; expect(action.y + action.height).toBeLessThanOrEqual(height);
    await page.getByRole('button',{name:'配置配套资源'}).click(); const editor = page.getByRole('dialog',{name:'配置配套资源 · 英语（美式）'}); await expect(editor).toBeVisible();
    await expect(editor.getByRole('combobox',{name:'G2P 模型'})).toHaveCount(0);
    await expect(editor.getByRole('button',{name:'添加本地G2P模型'})).toHaveCount(0);
    await editor.getByRole('combobox',{name:'MFA 发音词典'}).focus(); await page.keyboard.press('Tab'); await expect(editor.getByRole('button',{name:'添加本地词典'})).toBeFocused();
    await editor.getByRole('textbox',{name:'Conda 环境名'}).fill('draft');
    await page.screenshot({path:screenshot(`mfa-editor-draft-dark-${width}.png`)});
    const bounds = (await editor.boundingBox())!; expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(width); expect(bounds.height).toBeLessThanOrEqual(height);
    await page.keyboard.press('Escape'); await expect(editor).toHaveCount(0);
    await page.getByRole('textbox',{name:/参考文本/}).fill('hello'); await expect(page.getByRole('button',{name:'开始分析'})).toBeDisabled();
    await page.getByRole('button',{name:'转为手动 IPA'}).click(); await expect(target).toHaveValue('');
    await page.getByRole('button',{name:'开始分析'}).click();
    const params = await page.evaluate(() => (window as any).tutorCalls.find((c:any) => c.method === 'tutor.submit').params);
    expect(params).not.toHaveProperty('generation_id'); expect(params.phonemes).toBe('h ɛ l oʊ h ɛ l oʊ');
  }
});

test('generated request sends unique-word choices and server generation identity', async ({page}) => {
  await page.goto('/?window=pronunciation'); await page.getByRole('combobox',{name:'目标语言与读音'}).selectOption('en-US');
  await page.getByRole('textbox',{name:/参考文本/}).fill('hello hello'); await page.getByRole('button',{name:'自动生成音位'}).click();
  await page.getByRole('combobox',{name:'hello 的读音'}).selectOption('0'); await page.getByRole('button',{name:'确认并应用读音'}).click();
  await page.getByRole('button',{name:'开始分析'}).click();
  expect(await page.evaluate(() => (window as any).tutorCalls.at(-1))).toMatchObject({method:'tutor.submit',params:{preset_id:'en-US',generation_id:'generation-1',choices:{hello:0},phonemes:'h ə l ˈoʊ h ə l ˈoʊ'}});
});

test('late generation cannot overwrite new transcript and cancel keeps pending request busy', async ({page}) => {
  await page.addInitScript(() => {const original = (window as any).pywebview.api.rpc; (window as any).pywebview.api.rpc = (method:string,params:any) => method === 'tutor.generate_phonemes' ? new Promise(resolve => {(window as any).finishGeneration = () => original(method,params).then(resolve);}) : original(method,params);});
  await page.goto('/?window=pronunciation'); await page.getByRole('combobox',{name:'目标语言与读音'}).selectOption('en-US');
  await page.getByRole('textbox',{name:/参考文本/}).fill('hello hello'); await page.getByRole('button',{name:'自动生成音位'}).click();
  await page.getByRole('textbox',{name:/参考文本/}).fill('changed'); await page.getByRole('button',{name:'取消生成'}).click();
  await expect(page.getByRole('button',{name:'正在生成音位…'})).toBeDisabled(); await expect(page.getByRole('button',{name:'取消',exact:true})).toBeEnabled();
  await page.evaluate(() => (window as any).finishGeneration()); await expect(page.getByRole('button',{name:'自动生成音位'})).toBeEnabled();
  await expect(page.getByRole('textbox',{name:/目标音位/})).toHaveValue(''); await expect(page.getByRole('button',{name:'开始分析'})).toBeDisabled();
});
