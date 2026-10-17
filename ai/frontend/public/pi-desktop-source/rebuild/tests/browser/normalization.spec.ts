import {expect, test} from '@playwright/test';
import {join} from 'node:path';

for (const width of [1040, 600]) test(`normalization slots, explicit confirmation, z preview and bounded layout at ${width}px`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  await page.addInitScript(() => {
    (window as any).tutorCalls = [];
    (window as any).pywebview = {api: {rpc: async (method: string, params: Record<string, unknown>) => {
      (window as any).tutorCalls.push({method, params});
      if (method === 'tutor.bootstrap') return {sounds: [{id: '4', name: '标准音'}, {id: '9', name: '学习者'}], profiles: [{id: 'ja-JP-standard-v1', language: 'ja', name: '日语（标准语）'}], model: {source: '本地模型', name: 'fixture'}, theme: 'light', normalization: {version: 'normalization-v1', formants: false, inventories: {'ja-JP-standard-v1': ['a', 'i', 'ɯ', 'e', 'o']}}};
      if (method === 'tutor.import_calibration') return {id: params.role, speaker_label: params.role, status: 'applied', material: {wav_name: 'independent-calibration.wav', textgrid_name: 'calibration.TextGrid'}, formants: {f1: {status: 'applied', reason: '', coverage: {}}, f2: {status: 'applied', reason: '', coverage: {}}, f3: {status: 'insufficient_evidence', reason: '缺少 F3', coverage: {}}}};
      return {accepted: true};
    }}};
  });
  await page.goto('/?window=pronunciation');
  await page.getByRole('radio', {name: /音位判据/}).check();
  const toggle = page.getByRole('checkbox', {name: /Lobanov/});
  await expect(toggle).not.toBeChecked(); await toggle.check();
  for (const name of ['标准说话人校准', '学习者校准']) {
    await page.getByText(name, {exact: true}).locator('..').getByRole('button', {name: /导入 WAV/}).click();
  }
  await page.getByRole('checkbox', {name: /确认双方校准/}).check();
  await expect(page.getByText(/F1：预计应用 z 比较；F2：预计应用 z 比较；F3：证据不足/)).toBeVisible();
  const layout = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, width: innerWidth}));
  expect(layout.scroll).toBeLessThanOrEqual(layout.width + 1);
  if (process.env.PI_SCRATCH_DIR) await page.screenshot({path: join(process.env.PI_SCRATCH_DIR, `normalization-${width}.png`), fullPage: true});
  await page.getByRole('textbox', {name: /目标音位/}).fill('a');
  await page.getByRole('button', {name: '开始分析'}).click();
  await expect.poll(() => page.evaluate(() => (window as any).tutorCalls.filter((x: any) => x.method === 'tutor.submit').length)).toBe(1);
  const payload = await page.evaluate(() => (window as any).tutorCalls.find((x: any) => x.method === 'tutor.submit').params);
  expect(payload.normalization).toEqual({version: 'normalization-v1', formants: true, confirmed: true, reference_calibration_id: 'reference', learner_calibration_id: 'learner'});
});
