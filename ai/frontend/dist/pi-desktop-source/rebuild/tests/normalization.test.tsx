import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {PronunciationForm} from '../src/PronunciationForm';
import {bootstrapFixture} from './mfa-fixtures';

const bootstrap = {...bootstrapFixture, normalization: {version: 'normalization-v1', formants: false, inventories: {'en-US-general-v1': ['i', 'ɪ', 'ɛ', 'æ', 'ɑ', 'ɔ', 'ʊ', 'u', 'ʌ'], 'ja-JP-standard-v1': ['a', 'i', 'ɯ', 'e', 'o']}}};
const calibration = (id: string) => ({id, speaker_label: id, status: 'applied', material: {wav_name: 'independent.wav', textgrid_name: 'independent.TextGrid'}, formants: {f1: {status: 'applied', reason: '', coverage: {a: 3}}, f2: {status: 'applied', reason: '', coverage: {a: 3}}, f3: {status: 'insufficient_evidence', reason: '缺少 F3', coverage: {a: 0}}}});
const importButton = (label: string) => within(screen.getByText(label).parentElement!).getByRole('button', {name: /导入 WAV/});
const chooseRules = async () => {await screen.findByRole('combobox', {name: '目标语言与读音'}); fireEvent.click(screen.getByRole('radio', {name: /音位判据/}));};
beforeEach(() => {vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('defaults to raw and keeps normalization disabled in legacy submissions with explicit measurement settings', async () => {
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>);
  await chooseRules();
  expect((screen.getByRole('checkbox', {name: /Lobanov/}) as HTMLInputElement).checked).toBe(false);
  expect(screen.getByText('已关闭：仅原始 Hz 观察')).toBeTruthy();
  fireEvent.click(screen.getByRole('radio', {name: /声学对比/}));
  fireEvent.change(screen.getByRole('textbox', {name: /目标音位/}), {target: {value: 'a'}});
  fireEvent.click(screen.getByRole('button', {name: '开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit', expect.anything()));
  const payload = rpc.mock.calls.find(([method]) => method === 'tutor.submit')?.[1] as Record<string, unknown>;
  expect(payload.normalization).toMatchObject({version: 'normalization-v1', formants: false, confirmed: false});
});

it('imports both slots, previews F1/F2 only after confirmation and submits host receipts, not client fitted parameters', async () => {
  const rpc = vi.fn(async (method: string, params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : method === 'tutor.import_calibration' ? calibration(String(params.role)) : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>); await chooseRules();
  fireEvent.click(screen.getByRole('checkbox', {name: /Lobanov/}));
  fireEvent.click(importButton('标准说话人校准'));
  await screen.findByText('reference · independent.wav');
  expect(screen.getByText(/F1：证据不足/)).toBeTruthy();
  fireEvent.click(importButton('学习者校准'));
  await screen.findByText('learner · independent.wav');
  fireEvent.click(screen.getByRole('checkbox', {name: /确认双方校准/}));
  expect(screen.getByText(/F1：使用 z 值比较；F2：使用 z 值比较；F3：证据不足/)).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', {name: /目标音位/}), {target: {value: 'a'}});
  fireEvent.click(screen.getByRole('button', {name: '开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit', expect.objectContaining({normalization: {version: 'normalization-v1', formants: true, confirmed: true, reference_calibration_id: 'reference', learner_calibration_id: 'learner'}})));
});

it('clears imported identities and confirmation when changing speakers or reading presets', async () => {
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? {...bootstrap, normalization: {...bootstrap.normalization, formants: true}} : calibration('receipt'));
  render(<PronunciationForm rpc={rpc}/>); await chooseRules();
  fireEvent.click(importButton('标准说话人校准')); await screen.findByText('receipt · independent.wav');
  fireEvent.change(screen.getByRole('combobox', {name: '学习者录音'}), {target: {value: '4'}});
  expect(screen.queryByText('receipt · independent.wav')).toBeNull();
  expect((screen.getByRole('checkbox', {name: /确认双方校准/}) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(importButton('标准说话人校准')); await screen.findByText('receipt · independent.wav');
  fireEvent.change(screen.getByRole('combobox', {name: '判据预设'}), {target: {value: 'ja-JP-standard-v1'}});
  expect(screen.queryByText('receipt · independent.wav')).toBeNull();
  expect(screen.getByText(/本预设清单：\/a \/ \/i/)).toBeTruthy();
});

it('blocks submission during native import and leaves missing calibration as raw-only rather than installing anything', async () => {
  let finish!: (value: unknown) => void;
  const rpc = vi.fn((method: string) => method === 'tutor.bootstrap' ? Promise.resolve({...bootstrap, normalization: {...bootstrap.normalization, formants: true}}) : new Promise(resolve => {finish = resolve;}));
  render(<PronunciationForm rpc={rpc}/>); await chooseRules();
  fireEvent.click(importButton('标准说话人校准'));
  expect((screen.getByRole('button', {name: '开始分析'}) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => finish(null));
  expect((screen.getByRole('button', {name: '开始分析'}) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getAllByText('未导入：仅原始 Hz 观察')).toHaveLength(2);
  // 能力探测（tutor.capabilities）不改变这条链的意图：只允许 bootstrap + 原生导入，不安装任何东西。
  expect(rpc.mock.calls.map(([method]) => method).filter(method => method !== 'tutor.capabilities'))
    .toEqual(['tutor.bootstrap', 'tutor.import_calibration']);
});

it('refuses formant normalization up front when praat-parselmouth is missing', async () => {
  const capabilities = {ready:false, features:{formants:{label:'共振峰校准与 F1/F2 观察', ready:false, reason:'未安装/无法加载 praat-parselmouth（ModuleNotFoundError）', hint:'请在“路径配置 → 配置运行库…”安装；装好后关闭并重新打开纠音窗口。'}}};
  const restored = {...bootstrap, normalization:{...bootstrap.normalization, formants:true}};
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? restored : method === 'tutor.capabilities' ? capabilities : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>); await chooseRules();
  const formants = screen.getByRole('checkbox', {name: /Lobanov/}) as HTMLInputElement;
  await waitFor(() => expect(formants.checked).toBe(false));
  expect(formants.disabled).toBe(true);
  // 说清缺哪个包、去哪儿装；不提供「自动安装」之类的按钮。
  expect(screen.getAllByText(/共振峰校准与 F1\/F2 观察当前不可用：/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/praat-parselmouth/).length).toBeGreaterThan(0);
  expect(rpc.mock.calls.some(([method]) => method.includes('install'))).toBe(false);
});

it('submits independent Burg parameters in the actual legacy form without enabling normalization', async () => {
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? bootstrap : {accepted: true});
  render(<PronunciationForm rpc={rpc}/>);
  await screen.findByRole('combobox', {name: '目标语言与读音'});
  fireEvent.click(screen.getByText('高级设置'));
  fireEvent.change(screen.getByRole('spinbutton', {name: '标准音共振峰上限（Hz）'}), {target: {value: '5000'}});
  expect((screen.getByRole('spinbutton', {name: '学习者共振峰上限（Hz）'}) as HTMLInputElement).value).toBe('5500');
  fireEvent.change(screen.getByRole('textbox', {name: /目标音位/}), {target: {value: 'a'}});
  fireEvent.click(screen.getByRole('button', {name: '开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit', expect.objectContaining({normalization: expect.objectContaining({
    formants: false, measurement_settings: expect.objectContaining({reference: expect.objectContaining({maximum_formant_hz: '5000'})}),
  })})));
});
