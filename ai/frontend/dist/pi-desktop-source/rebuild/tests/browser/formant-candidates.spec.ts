import {expect, test} from '@playwright/test';
import {join} from 'node:path';

test('candidate analysis and manual adoption stay usable at narrow widths without changing the other speaker', async ({page}) => {
  await page.addInitScript(() => {
    const parameters = {maximum_formant_hz: 5000, max_number_of_formants: 5, time_step_sec: .01, window_length_sec: .025, pre_emphasis_from_hz: 50};
    const record = {id: 'native-receipt', speaker_label: 'reference-speaker', status: 'applied', measurement_settings: parameters,
      material: {wav_name: 'independent.wav', textgrid_name: 'independent.TextGrid'},
      formants: Object.fromEntries(['f1', 'f2', 'f3'].map(feature => [feature, {status: 'applied', reason: '', coverage: {a: 3}}]))};
    (window as any).formantRequests = [];
    (window as any).pywebview = {api: {rpc: async (method: string, params: unknown) => {
      (window as any).formantRequests.push({method, params});
      if (method === 'tutor.bootstrap') return {sounds: [{id: '4', name: '标准音'}, {id: '9', name: '学习者'}], profiles: [{id: 'ja-JP-standard-v1', name: '日语标准语', language: 'ja'}], model: {source: 'local', name: 'fixture'}, theme: 'light',
        normalization: {version: 'normalization-v1', formants: false, inventories: {'ja-JP-standard-v1': ['a', 'i', 'ɯ', 'e', 'o']}}};
      if (method === 'tutor.analyze_formant_candidates') return {id: 'analysis', material: {wav_name: 'independent.wav'}, decision: {status: 'uncertain', recommended_id: null, reason: '候选无法可靠区分，未自动改变参数'},
        candidates: [{id: 'candidate', parameters, quality: {status: 'eligible', score: 0, reason: ''}, can_apply: true}]};
      if (method === 'tutor.select_formant_candidate') return record;
      return {accepted: true};
    }}};
  });
  for (const [width, height] of [[1080, 850], [620, 600], [375, 812]]) {
    await page.setViewportSize({width, height});
    await page.goto('/?window=pronunciation');
    await page.getByRole('radio', {name: /音位判据/}).check();
    await page.getByText('高级设置', {exact: true}).click();
    await page.getByRole('button', {name: '标准音：自动分析并选参'}).click();
    await expect(page.getByText(/候选无法可靠区分/)).toBeVisible();
    await expect(page.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'})).toHaveValue('5500');
    await page.getByRole('button', {name: '标准音采用 5000 Hz 候选'}).click();
    await expect(page.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'})).toHaveValue('5000');
    await expect(page.getByRole('spinbutton', {name: '学习者共振峰上限（Hz）'})).toHaveValue('5500');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const footer = await page.getByRole('button', {name: '开始分析'}).boundingBox();
    expect(footer!.y + footer!.height).toBeLessThanOrEqual(height);
    await page.screenshot({path: process.env.PI_SCRATCH_DIR ? join(process.env.PI_SCRATCH_DIR, `formant-candidates-${width}.png`) : test.info().outputPath(`formant-candidates-${width}.png`)});
  }
});
