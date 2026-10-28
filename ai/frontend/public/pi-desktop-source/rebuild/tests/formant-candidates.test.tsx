import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {PronunciationForm} from '../src/PronunciationForm';

const settings = {maximum_formant_hz: 5000, max_number_of_formants: 5, time_step_sec: .01, window_length_sec: .025, pre_emphasis_from_hz: 50};
const bootstrap = {sounds: [{id: '4', name: 'reference'}, {id: '9', name: 'learner'}], profiles: [{id: 'ja-JP-standard-v1', name: '日语标准语', language: 'ja'}], model: {source: 'local', name: 'fixture'}, theme: 'light',
  normalization: {version: 'normalization-v1', formants: false, inventories: {'ja-JP-standard-v1': ['a', 'i', 'ɯ', 'e', 'o']}}};
const receipt = {id: 'receipt-reference', speaker_label: 'reference-speaker', status: 'applied', measurement_settings: settings,
  material: {wav_name: 'independent.wav', textgrid_name: 'independent.TextGrid'},
  formants: Object.fromEntries(['f1', 'f2', 'f3'].map(name => [name, {status: 'applied', reason: '', coverage: {a: 3}}]))};
const analysis = {id: 'analysis-reference', material: {wav_name: 'independent.wav'}, decision: {status: 'uncertain', recommended_id: null, reason: '候选差异不足以可靠区分'},
  candidates: [{id: 'candidate-5000', parameters: settings, quality: {status: 'eligible', score: 0, reason: ''}, can_apply: true},
    {id: 'candidate-6500', parameters: {...settings, maximum_formant_hz: 6500}, quality: {status: 'unavailable', score: null, reason: '超过源带宽'}, can_apply: false}]};
const ready = async () => {
  await screen.findByRole('combobox', {name: '目标语言与读音'});
  fireEvent.click(screen.getByRole('radio', {name: /音位判据/}));
  fireEvent.click(screen.getByText('高级设置'));
};
beforeEach(() => {vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('does not force an ambiguous ceiling and manually adopts a trusted candidate for only one speaker', async () => {
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : method === 'tutor.analyze_formant_candidates' ? analysis : method === 'tutor.select_formant_candidate' ? receipt : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: '标准音：自动分析并选参'}));
  await screen.findByText(/候选差异不足以可靠区分/);
  expect((screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5500');
  expect((screen.getByRole('button', {name: '标准音采用 6500 Hz 候选'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', {name: '标准音采用 5000 Hz 候选'}));
  await waitFor(() => expect((screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5000'));
  expect((screen.getByRole('spinbutton', {name: '学习者共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5500');
  fireEvent.change(screen.getByRole('textbox', {name: /目标音位/}), {target: {value: 'a'}});
  fireEvent.click(screen.getByRole('button', {name: '开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit', expect.objectContaining({normalization: expect.objectContaining({
    formants: false, reference_calibration_id: receipt.id, measurement_settings: expect.objectContaining({reference: settings}),
  })})));
  const choice = rpc.mock.calls.find(([method]) => method === 'tutor.select_formant_candidate')?.[1];
  expect(choice).toMatchObject({role: 'reference', sound_id: '4', analysis_id: analysis.id, candidate_id: 'candidate-5000'});
});

it('applies a clear automatic suggestion and synchronizes the existing calibration slot', async () => {
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : method === 'tutor.analyze_formant_candidates'
    ? {...analysis, decision: {status: 'suggested', recommended_id: 'candidate-5000', reason: '明确建议'}, applied_calibration: receipt} : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: '标准音：自动分析并选参'}));
  await waitFor(() => expect((screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5000'));
  fireEvent.click(screen.getByRole('checkbox', {name: /Lobanov/}));
  expect(await screen.findByText('reference-speaker · independent.wav')).toBeTruthy();
  expect(rpc.mock.calls.some(([method]) => method === 'tutor.select_formant_candidate')).toBe(false);
  fireEvent.change(screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}), {target: {value: '5200'}});
  await waitFor(() => expect(screen.queryByText('reference-speaker · independent.wav')).toBeNull());
});

it('blocks submission while analyzing and ignores late results after the speaker context changes', async () => {
  let finish: ((value: unknown) => void) | undefined;
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : method === 'tutor.analyze_formant_candidates' ? new Promise(resolve => {finish = resolve;}) : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>); await ready();
  fireEvent.click(screen.getByRole('button', {name: '标准音：自动分析并选参'}));
  expect((screen.getByRole('button', {name: '开始分析'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', {name: /声学对比/}));
  await act(async () => {finish?.({...analysis, applied_calibration: receipt});});
  expect((screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5500');
});
