import {expect, test, type Page} from '@playwright/test';
import {join} from 'node:path';
import {mfaFixture} from '../mfa-fixtures';

const scales = [.8, 1, 1.25, 1.5, 2];
async function checkCopy(page: Page) {
  const copy = await page.locator('small, .muted, .pron-help, .pron-notice, .mfa-note, .mfa-status, .popover-footnote, .pron-normalization p').evaluateAll(nodes =>
    nodes.filter(node => node.checkVisibility()).map(node => node.textContent || ''));
  expect(copy.filter(text => /。|不代表|不等于|不会假装|不会伪装|虚假的|以.*为准/.test(text))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}
async function scale(page: Page, value: number) {
  await page.evaluate(value => {document.documentElement.style.zoom = String(value);}, value);
}
async function capture(page: Page, name: string) {
  await page.screenshot({path: process.env.PI_SCRATCH_DIR ? join(process.env.PI_SCRATCH_DIR, name + '.png') : test.info().outputPath(name + '.png')});
}

test('settings helper copy stays concise and readable from 80 to 200 percent', async ({page}) => {
  await page.emulateMedia({colorScheme:'light', reducedMotion:'reduce'});
  for (const value of scales) {
    await page.goto('/?demo=1');
    await scale(page, value);
    await page.getByRole('button', {name:'打开设置', exact:true}).click();
    const nav = page.getByRole('navigation', {name:'设置分类'});
    for (const name of ['模型', '上下文与输出', '工作流程', 'Praat 工具与音频', '外观与交互', '会话与存储']) {
      await nav.getByRole('button', {name, exact:true}).click();
      await checkCopy(page);
      if (name === '模型' || name === '上下文与输出') await capture(page, `copy-settings-${name}-${value}`);
      if (name === '上下文与输出') {
        await expect(nav.getByRole('button', {name:'采样与思考', exact:true})).toHaveCount(0);
        await expect(page.getByRole('heading', {name:'上下文压缩与专业证据', exact:true})).toHaveCount(0);
        const thinking = page.getByRole('combobox', {name:'思考档位', exact:true});
        await thinking.scrollIntoViewIfNeeded();
        await expect(thinking).toBeInViewport();
        await capture(page, `copy-settings-thinking-${value}`);
      }
    }
  }
});

test('pronunciation, resource and Anki helper copy at all zoom levels', async ({page}) => {
  await page.addInitScript(mfa => {
    (window as any).pywebview = {api:{rpc:async (method: string) => {
      if (method === 'tutor.bootstrap') return {sounds:[{id:'4',name:'标准音',duration:1},{id:'9',name:'学习者',duration:1}], profiles:[{id:'en-US-general-v1',name:'英语（美式）',language:'en'}], model:{source:'本地模型',name:'UI fixture'}, theme:'light', normalization:{inventories:{'en-US-general-v1':['i','u','a']}}};
      if (method === 'tutor.capabilities') return {features:{}};
      if (method === 'mfa.list') return mfa;
      if (method === 'anki.settings') return {settings:{media_dir:'',ffmpeg:'',fallback:true,audio_field:'Audio',start_field:'Start',end_field:'End',mode:'both',magnifier_selector:'.search-link'},code:'const AIPRAAT_ENTRY = "both";'};
      return {dictionaries:[],models:[],theme:'light'};
    }}};
  }, mfaFixture);
  for (const value of scales) {
    await page.goto('/?window=pronunciation');
    await expect(page.getByRole('button', {name:'开始分析'})).toBeVisible();
    await scale(page, value);
    await page.getByRole('radio', {name:/音位判据/}).check();
    await page.getByRole('checkbox', {name:/相对音高（半音/}).check();
    await checkCopy(page);
    await capture(page, `copy-pronunciation-${value}`);
    await page.goto('/?window=dictionaries');
    await scale(page, value);
    await page.getByRole('button', {name:'纠音资源配套',exact:true}).click();
    await page.getByRole('button', {name:'配置 英语（美式） 配套资源'}).click();
    await checkCopy(page);
    await capture(page, `copy-mfa-${value}`);
    await page.goto('/?window=anki');
    await expect(page.getByRole('radio')).toHaveCount(3);
    await scale(page, value);
    await checkCopy(page);
    await capture(page, `copy-anki-${value}`);
  }
});
