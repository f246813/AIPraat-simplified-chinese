import {expect, test, type Locator, type Page} from '@playwright/test';
import {join} from 'node:path';
import {mfaFixture} from '../mfa-fixtures';

const sizes = [[1080,850],[620,600],[375,812]] as const;
async function capture(page: Page, name: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const file=`forms-${name}.png`;
  await page.screenshot({path:process.env.PI_SCRATCH_DIR ? join(process.env.PI_SCRATCH_DIR,file) : test.info().outputPath(file)});
}
async function switchSize(control: Locator) {
  await expect(control).toHaveAttribute('type','checkbox');
  await expect(control).toHaveCSS('width','32px');
  await expect(control).toHaveCSS('height','20px');
  expect(await control.evaluate(node => getComputedStyle(node,'::before').width)).toBe('16px');
}

test('settings retain all categories, native switch interaction and square lock at narrow widths',async({page})=>{
  await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
  for (const [width,height] of sizes) {
    await page.setViewportSize({width,height}); await page.goto('/?demo=1');
    await page.getByRole('button',{name:'打开设置',exact:true}).click();
    const nav = page.getByRole('navigation',{name:'设置分类'});
    await nav.getByRole('button',{name:'模型',exact:true}).click();
    const control = page.getByRole('checkbox',{name:'显式清除已保存 API Key',exact:true});
    const lock = page.getByRole('checkbox',{name:'锁定云端 API',exact:true});
    await switchSize(control);
    await expect(lock).toHaveAttribute('data-toggle','lock');
    await expect(lock).toHaveCSS('width','16px');
    await expect(lock).toHaveCSS('appearance','auto');
    const before = await control.isChecked();
    await control.locator('..').locator('span').click();
    expect(await control.isChecked()).toBe(!before);
    await control.focus(); await page.keyboard.press('Space');
    expect(await control.isChecked()).toBe(before);
    const text = page.getByRole('textbox',{name:'服务商名称',exact:true});
    await text.focus();
    await expect(text).toHaveCSS('outline-width','1px');
    await expect(text).toHaveCSS('box-shadow','none');
    await expect(page.locator('.settings-main')).toHaveCSS('background-color','rgba(255, 255, 255, 0.95)');
    await expect(page.locator('.main-surface')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    for (const category of ['模型','上下文与输出','工作流程','Praat 工具与音频','外观与交互','会话与存储','已归档会话']) {
      await nav.getByRole('button',{name:category,exact:true}).click();
      if (category==='工作流程') await switchSize(page.getByRole('checkbox',{name:'L0 专用工具执行',exact:true}));
      await page.locator('.settings-main').evaluate(node => node.scrollTop=0);
      await capture(page,`settings-${category}-${width}`);
    }
  }
});

test('pronunciation keeps native radios, shared switches, reachable footer and single painted surface',async({page})=>{
  await page.addInitScript(() => {
    (window as any).pywebview={api:{rpc:async(method:string)=>method==='tutor.bootstrap'?{
      sounds:[{id:'4',name:'标准音_hello'},{id:'9',name:'学习者_hello'}],
      profiles:[{id:'en-US-general-v1',name:'英语（美式）',language:'en'}],
      model:{source:'本地模型',name:'browser-test'},theme:'light',
    }:{accepted:true}}};
  });
  for (const [width,height] of sizes) {
    await page.setViewportSize({width,height}); await page.goto('/?window=pronunciation');
    await expect(page.getByRole('button',{name:'开始分析'})).toBeVisible();
    await expect(page.locator('.pron-window')).toHaveCSS('background-color','rgba(255, 255, 255, 0.95)');
    const radio=page.getByRole('radio',{name:/声学对比/});
    await expect(radio).toHaveCSS('appearance','auto'); await expect(radio).toHaveCSS('width','16px');
    await capture(page,`pronunciation-${width}`);
    const explain=page.getByRole('checkbox',{name:/使用 AI 生成解释/});
    await switchSize(explain); await explain.scrollIntoViewIfNeeded();
    await explain.focus(); await page.keyboard.press('Space'); await expect(explain).not.toBeChecked();
    await page.getByText('高级设置').click();
    const vision=page.getByRole('checkbox',{name:/AI 视觉解释/});
    await switchSize(vision); await vision.check();
    await capture(page,`pronunciation-advanced-${width}`);
    const bounds=(await page.getByRole('button',{name:'开始分析'}).boundingBox())!;
    expect(bounds.y+bounds.height).toBeLessThanOrEqual(height);
  }
});

test('resource navigation and MFA pairing share white cards and usable small modal controls',async({page})=>{
  await page.addInitScript(mfa=>{
    (window as any).pywebview={api:{rpc:async(method:string)=>{
      if(method==='mfa.list')return mfa;
      if(method.startsWith('acoustic_models.'))return {models:[{name:'English acoustic.zip',path:'C:/资源/English acoustic.zip',active:true,exists:true}],theme:'light'};
      if(method.startsWith('wav2vec2_models.'))return {models:[{name:'Wav2Vec2模型',path:'C:/资源/Wav2Vec2',active:true,exists:true}],theme:'light'};
      return {dictionaries:[{name:'English.dict',path:'C:/词典/English.dict',active:true,exists:true}],theme:'light'};
    }}};
  },mfaFixture);
  for(const [width,height] of sizes){
    await page.setViewportSize({width,height}); await page.goto('/?window=dictionaries');
    await expect(page.getByRole('button',{name:'添加MFA语音词典'})).toBeVisible();
    await expect(page.locator('.dictionary-window')).toHaveCSS('background-color','rgba(255, 255, 255, 0.95)');
    for(const tab of ['MFA语音词典','MFA声学模型','Wav2Vec2模型','MFA G2P 模型','纠音资源配套']){
      await page.getByRole('button',{name:tab,exact:true}).click();
      await capture(page,`resources-${tab}-${width}`);
    }
    await page.getByRole('button',{name:'配置 英语（美式） 配套资源'}).click();
    const dialog=page.getByRole('dialog',{name:'配置配套资源 · 英语（美式）'});
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('checkbox',{name:/我确认/})).toHaveCount(0);
    const defaultPreset=dialog.getByRole('checkbox',{name:/默认预设/});
    await switchSize(defaultPreset); await defaultPreset.focus(); await page.keyboard.press('Space');
    await capture(page,`mfa-editor-${width}`);
    await dialog.getByRole('textbox',{name:'Conda 环境名'}).fill('draft-not-saved');
    await dialog.getByRole('button',{name:'关闭资源配置'}).click();
    await expect(dialog).toHaveCount(0);
  }
});

test('Anki retains mutually exclusive radios and every lower section on small pages',async({page})=>{
  await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
  await page.addInitScript(()=>{
    const settings={media_dir:'',ffmpeg:'',fallback:true,audio_field:'Audio',start_field:'Start',end_field:'End',mode:'both',magnifier_selector:'.search-link'};
    (window as any).pywebview={api:{rpc:async(method:string)=>method==='anki.settings'?{settings,code:'const AIPRAAT_ENTRY = "both";'}:{message:'已保存'}}};
  });
  for(const [width,height] of [[1080,850],[660,540],[375,812]]){
    await page.setViewportSize({width,height}); await page.goto('/?window=anki');
    await expect(page.getByRole('radio')).toHaveCount(3);
    await expect(page.getByRole('radio',{name:'保留两个入口'})).toBeChecked();
    await expect(page.getByRole('radio').first()).toHaveCSS('appearance','auto');
    await expect(page.getByRole('radio').first()).toHaveCSS('width','16px');
    await expect(page.locator('.anki-integration')).toHaveCSS('background-color','rgba(255, 255, 255, 0.95)');
    await capture(page,`anki-${width}`);
    await page.getByRole('radio',{name:'复用原放大镜',exact:true}).check();
    await expect(page.getByRole('radio',{name:'保留两个入口'})).not.toBeChecked();
    await page.getByRole('button',{name:'导出可编辑的 entry.js'}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button',{name:'导出可编辑的 entry.js'})).toBeInViewport();
    await capture(page,`anki-bottom-${width}`);
  }
});

test('settings panels fill a wide window and use the muted blue palette for actions, switches and sliders',async({page})=>{
  await page.setViewportSize({width:1400,height:900});
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'打开设置',exact:true}).click();
  await page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name:'模型',exact:true}).click();
  // The old 900px cap left a blank right column once the window grew past it.
  const main=(await page.locator('.settings-main').boundingBox())!;
  const card=(await page.locator('.settings-main .card').first().boundingBox())!;
  expect(card.width).toBeGreaterThan(1000);
  expect(main.x+main.width-(card.x+card.width)).toBeLessThan(48);
  await expect(page.locator('.settings-main .card').first()).toHaveCSS('max-width','none');
  await expect(page.locator('.settings-main>header')).toHaveCSS('max-width','none');
  // Large actions use the more muted blue; only the composer send keeps the saturated one.
  await expect(page.getByRole('button',{name:'保存设置',exact:true})).toHaveCSS('background-color','rgb(65, 103, 170)');
  const control=page.getByRole('checkbox',{name:'显式清除已保存 API Key',exact:true});
  if(await control.isChecked())await control.locator('..').locator('span').click();
  await expect(control).toHaveCSS('background-color','rgb(221, 229, 241)');
  await control.locator('..').locator('span').click();
  await expect(control).toHaveCSS('background-color','rgb(63, 116, 200)');
  await capture(page,'settings-wide-1400');
  await control.locator('..').locator('span').click();
  // Long sliders keep a white round thumb over a blue-filled track.
  await page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name:'外观与交互',exact:true}).click();
  const slider=page.locator('.range-field input[type=range]').first();
  await expect(slider).toBeEnabled();
  await expect(slider).toHaveCSS('appearance','none');
  const progress=await slider.evaluate(el=>el.style.getPropertyValue('--range-progress'));
  expect(progress).toMatch(/%$/);
  // Visual evidence for the white round thumb over the blue-filled track.
  const mid=await slider.evaluate(el=>{const input=el as HTMLInputElement;return String((Number(input.min)+Number(input.max))/2);});
  await slider.fill(mid);
  const box=(await slider.boundingBox())!;
  await page.screenshot({path:process.env.PI_SCRATCH_DIR?join(process.env.PI_SCRATCH_DIR,'settings-sliders.png'):test.info().outputPath('settings-sliders.png'),clip:{x:Math.max(0,box.x-20),y:Math.max(0,box.y-20),width:box.width+40,height:box.height+40}});
});

test('data protection controls switch the empty-session policy and lock the mode while saving',async({page})=>{
  await page.setViewportSize({width:1080,height:850});
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'打开设置',exact:true}).click();
  await page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name:'会话与存储',exact:true}).click();
  const keep=page.getByRole('checkbox',{name:'保存空会话'});
  const mode=page.getByRole('combobox',{name:'空白会话处理'});
  await expect(keep).not.toBeChecked();
  await expect(mode).toBeEnabled();
  await expect(mode).toHaveValue('delete_on_exit');
  await mode.selectOption('never_persist');
  await expect(mode).toHaveValue('never_persist');
  // Turning the save switch on locks the path choice.
  await keep.click();
  await expect(keep).toBeChecked();
  await expect(mode).toBeDisabled();
  await capture(page,'settings-data-protection');
});
