import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PronunciationForm } from '../src/PronunciationForm';

import { bootstrapFixture as data, mfaFixture, generationFixture } from './mfa-fixtures';
beforeEach(() => {vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})); HTMLDialogElement.prototype.showModal = function() {this.setAttribute('open', '');};});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('submits selected sounds and AI options through the real tutor namespace', async () => {
  const rpc = vi.fn().mockResolvedValueOnce(data).mockResolvedValue({accepted:true});
  render(<PronunciationForm rpc={rpc}/>);
  await screen.findByText(/当前模型：本地模型 · local-fixture/);
  fireEvent.change(screen.getByRole('textbox',{name:/目标音位/}), {target:{value:'aː'}});
  fireEvent.change(screen.getByRole('combobox',{name:'标准音范本'}), {target:{value:'9'}});
  fireEvent.change(screen.getByRole('combobox',{name:'学习者录音'}), {target:{value:'4'}});
  fireEvent.click(screen.getByRole('checkbox',{name:/使用 AI 生成解释/}));
  fireEvent.click(screen.getByText('高级设置'));
  fireEvent.click(screen.getByRole('checkbox',{name:/AI 视觉解释/}));
  fireEvent.click(screen.getByRole('button',{name:'开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('tutor.submit', expect.objectContaining({reference:'9',learner:'4',explain:false,vision:true,profile_id:null})));
  expect(screen.getByRole('button',{name:'正在提交…'}).hasAttribute('disabled')).toBe(true);
});

it('shows empty IPA inline and does not submit; selects Japanese rules and disables threshold', async () => {
  const rpc = vi.fn().mockResolvedValue(data);
  render(<PronunciationForm rpc={rpc}/>); await screen.findByText(/当前模型：本地模型 · local-fixture/);
  fireEvent.change(screen.getByRole('textbox',{name:/目标音位/}),{target:{value:' ,； '}});
  fireEvent.click(screen.getByRole('button',{name:'开始分析'}));
  expect(screen.getByRole('alert').textContent).toContain('IPA'); expect(rpc).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole('textbox',{name:/目标音位/}),{target:{value:'aː'}});
  fireEvent.click(screen.getByRole('radio',{name:/音位判据/}));
  fireEvent.change(screen.getByRole('combobox',{name:'判据预设'}),{target:{value:'ja-JP-standard-v1'}});
  expect((screen.getByRole('textbox',{name:/目标语言/}) as HTMLInputElement).value).toBe('ja');
  expect(screen.getByRole('spinbutton',{name:/声学对比阈值/}).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('tutor.submit',expect.objectContaining({language:'ja',profile_id:'ja-JP-standard-v1',phonemes:'aː'})));
});

it('retains entries after a host error and allows retry; cancel uses a separate operation', async () => {
  const rpc = vi.fn().mockResolvedValueOnce(data).mockRejectedValueOnce(new Error('录音读取失败')).mockResolvedValue({accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await screen.findByText(/当前模型：本地模型 · local-fixture/);
  fireEvent.change(screen.getByRole('textbox',{name:/目标音位/}),{target:{value:'aː'}});
  fireEvent.click(screen.getByRole('button',{name:'开始分析'}));
  await screen.findByText('录音读取失败');
  expect((screen.getByRole('textbox',{name:/目标音位/}) as HTMLInputElement).value).toBe('aː');
  fireEvent.click(screen.getByRole('button',{name:'取消'}));
  await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('tutor.cancel',{}));
});

it('displays configured cloud model rather than claiming Qwen', async () => {
  const rpc = vi.fn().mockResolvedValue({...data,theme:'dark',model:{source:'云端 API',name:'cloud-fixture'}});
  render(<PronunciationForm rpc={rpc}/>);
  await screen.findByText(/当前模型：云端 API · cloud-fixture/);
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(screen.queryByText(/Qwen/)).toBeNull();
});

const selectUS = async () => {await screen.findByRole('combobox',{name:'目标语言与读音'}); fireEvent.change(screen.getByRole('combobox',{name:'目标语言与读音'}), {target:{value:'en-US'}}); fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'hello hello'}});};
const ipa = () => screen.getByRole('textbox',{name:/目标音位/}) as HTMLInputElement;
const start = () => screen.getByRole('button',{name:'开始分析'}) as HTMLButtonElement;
it('starts in empty manual cmn mode unless a default is configured; UK rules and missing generation are disabled', async () => {
  const rpc = vi.fn().mockResolvedValue(data); render(<PronunciationForm rpc={rpc}/>);
  await screen.findByRole('combobox',{name:'目标语言与读音'});
  expect(ipa().value).toBe(''); expect((screen.getByRole('textbox',{name:/目标语言代码/}) as HTMLInputElement).value).toBe('cmn');
  fireEvent.change(screen.getByRole('combobox',{name:'目标语言与读音'}),{target:{value:'en-GB'}});
  expect((screen.getByRole('radio',{name:/音位判据/}) as HTMLInputElement).disabled).toBe(true);
  expect(screen.queryByRole('combobox',{name:'判据预设'})).toBeNull();
  fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'hello'}});
  expect((screen.getByRole('button',{name:'自动生成音位'}) as HTMLButtonElement).disabled).toBe(true); expect(start().disabled).toBe(true);
  expect(screen.getByText(/请添加英式 G2P/)).toBeTruthy(); expect(rpc).toHaveBeenCalledTimes(1);
});
it('restores configured default and generates unique words in original repeated-word order, submitting server identity and choices', async () => {
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? {...data,mfa:{...mfaFixture,default_preset:'en-US'}} : method === 'tutor.generate_phonemes' ? generationFixture : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await screen.findByRole('button',{name:'自动生成音位'});
  expect((screen.getByRole('combobox',{name:'目标语言与读音'}) as HTMLSelectElement).value).toBe('en-US');
  fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'hello hello'}});
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  await waitFor(() => expect(ipa().value).toBe('h ə l ˈoʊ h ə l ˈoʊ'));
  fireEvent.click(screen.getByRole('radio',{name:/音位判据/})); expect(screen.queryByRole('combobox',{name:'判据预设'})).toBeNull();
  fireEvent.click(start()); await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit',expect.objectContaining({preset_id:'en-US',generation_id:'generation-1',choices:{hello:0},phonemes:'h ə l ˈoʊ h ə l ˈoʊ',profile_id:'en-US-general-v1'})));
});
it('requires all multiple candidates to be chosen and applied, preserving manually entered IPA until explicit application', async () => {
  const multi = {...generationFixture,words:[{...generationFixture.words[0],candidates:[...generationFixture.words[0].candidates,{raw:['HH','EH','L','OW'],ipa:['h','ɛ','l','oʊ'],source:'词典'}]}]};
  const rpc = vi.fn(async (method: string, _params: Record<string, unknown>) => method === 'tutor.bootstrap' ? data : method === 'tutor.generate_phonemes' ? multi : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await screen.findByRole('combobox',{name:'目标语言与读音'});
  fireEvent.change(ipa(),{target:{value:'aː'}}); await selectUS(); fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  await screen.findByRole('combobox',{name:'hello 的读音'}); expect(ipa().value).toBe('aː'); expect(start().disabled).toBe(true);
  expect((screen.getByRole('button',{name:'确认并应用读音'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole('combobox',{name:'hello 的读音'}),{target:{value:'1'}}); expect(start().disabled).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'确认并应用读音'})); expect(ipa().value).toBe('h ɛ l oʊ h ɛ l oʊ'); expect(start().disabled).toBe(false);
  fireEvent.change(ipa(),{target:{value:'a'}}); expect(screen.getByText(/已转为手动 IPA/)).toBeTruthy(); fireEvent.click(start());
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit',expect.objectContaining({phonemes:'a'})));
  const params = rpc.mock.calls.find(([method]) => method === 'tutor.submit')?.[1] as Record<string, unknown>;
  expect(params).not.toHaveProperty('generation_id'); expect(params).not.toHaveProperty('preset_id');
});
it('invalidates applied results on transcript changes and preserves old IPA for explicit manual conversion', async () => {
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? data : generationFixture); render(<PronunciationForm rpc={rpc}/>); await selectUS();
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'})); await waitFor(() => expect(start().disabled).toBe(false));
  fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'other'}}); expect(start().disabled).toBe(true); expect(ipa().value).toContain('h ə');
  fireEvent.click(screen.getByRole('button',{name:'转为手动 IPA'})); expect(start().disabled).toBe(false);
});
it.each(['text','preset','cancel','close','unmount'])('ignores late results after %s, retaining busy until generation settles', async change => {
  let resolve!: (value: unknown) => void; const pending = new Promise(resolvePromise => {resolve = resolvePromise;});
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? data : method === 'tutor.generate_phonemes' ? pending : {cancelled:true});
  const view = render(<PronunciationForm rpc={rpc}/>); await selectUS(); fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  expect(start().disabled).toBe(true); expect((screen.getByRole('button',{name:'取消'}) as HTMLButtonElement).disabled).toBe(false);
  if (change === 'text') fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'other'}});
  if (change === 'preset') fireEvent.change(screen.getByRole('combobox',{name:'目标语言与读音'}),{target:{value:'en-GB'}});
  if (change === 'cancel') {fireEvent.click(screen.getByRole('button',{name:'取消生成'})); await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.cancel_generation',{})); expect((screen.getByRole('button',{name:'正在生成音位…'}) as HTMLButtonElement).disabled).toBe(true);}
  if (change === 'unmount') {view.unmount(); expect(rpc).toHaveBeenCalledWith('tutor.cancel_generation',{});}
  if (change === 'close') {fireEvent.click(screen.getByRole('button',{name:'取消'})); await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.cancel',{}));}
  await act(async () => resolve(generationFixture)); if (change !== 'unmount') {expect(ipa().value).toBe(''); expect((screen.getByRole('button',{name:change === 'close' ? '正在提交…' : '开始分析'}) as HTMLButtonElement).disabled).toBe(true);}
});
it('allows generation without acoustic resources, but blocks analysis and keeps host failures actionable', async () => {
  const withoutAcoustic = {...data,mfa:{...mfaFixture,presets:mfaFixture.presets.map(p => p.id === 'en-US' ? {...p,acoustic:'',status:{...p.status,acoustic:'missing'}} : p)}};
  const rpc = vi.fn().mockResolvedValueOnce(withoutAcoustic).mockRejectedValueOnce(new Error('请检查 MFA 路径')).mockResolvedValueOnce(generationFixture);
  render(<PronunciationForm rpc={rpc}/>); await selectUS(); fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  await screen.findByText('请检查 MFA 路径'); expect((screen.getByRole('textbox',{name:/参考文本/}) as HTMLInputElement).value).toBe('hello hello');
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'})); await screen.findByRole('combobox',{name:'hello 的读音'}); expect(ipa().value).toContain('h ə'); expect(start().disabled).toBe(true);
});
it('saved resource changes invalidate generated identity; imported resources alone do not overwrite IPA', async () => {
  const changed = {...mfaFixture,presets:mfaFixture.presets.map(p => p.id === 'en-US' ? {...p,phone_set:'ipa' as const} : p)};
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? data : method === 'tutor.generate_phonemes' ? generationFixture : changed);
  render(<PronunciationForm rpc={rpc}/>); await selectUS(); fireEvent.click(screen.getByRole('button',{name:'自动生成音位'})); await waitFor(() => expect(start().disabled).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'配置配套资源'})); const editor = await screen.findByRole('dialog');
  fireEvent.change(within(editor).getByRole('combobox',{name:'音位体系'}),{target:{value:'ipa'}}); fireEvent.click(within(editor).getByRole('button',{name:'保存配套'}));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull()); expect(start().disabled).toBe(true); expect(ipa().value).toContain('h ə');
});
it('shows the bound model, allows changing or clearing it, and invalidates generated readings after Save', async () => {
  const second = {...mfaFixture.resources.g2p[0],id:'g2',name:'Alternative G2P.zip',used_by:[]};
  const models = {...mfaFixture,resources:{...mfaFixture.resources,g2p:[...mfaFixture.resources.g2p,second]}};
  const rpc = vi.fn(async (method: string, params?: Record<string, unknown>) => method === 'tutor.bootstrap' ? {...data,mfa:models} : method === 'tutor.generate_phonemes' ? generationFixture : {...models,presets:models.presets.map(p => p.id === 'en-US' ? {...p,g2p:(params!.preset as {g2p:string}).g2p} : p)});
  render(<PronunciationForm rpc={rpc}/>); await selectUS();
  expect((screen.getByRole('combobox',{name:'G2P 模型'}) as HTMLSelectElement).value).toBe('g');
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'})); await waitFor(() => expect(start().disabled).toBe(false));
  fireEvent.change(screen.getByRole('combobox',{name:'G2P 模型'}),{target:{value:'g2'}});
  expect(within(screen.getByRole('dialog')).queryByRole('combobox',{name:'G2P 模型'})).toBeNull();
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:'关闭资源配置'}));
  expect(start().disabled).toBe(false);
  fireEvent.change(screen.getByRole('combobox',{name:'G2P 模型'}),{target:{value:'g2'}});
  fireEvent.click(screen.getByRole('button',{name:'保存配套'})); await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(rpc).toHaveBeenCalledWith('mfa.save',expect.objectContaining({preset:expect.objectContaining({g2p:'g2'})}));
  expect(start().disabled).toBe(true); expect(ipa().value).toContain('h ə');
  expect((screen.getByRole('combobox',{name:'G2P 模型'}) as HTMLSelectElement).value).toBe('g2');
  fireEvent.change(screen.getByRole('combobox',{name:'G2P 模型'}),{target:{value:''}});
  expect(within(screen.getByRole('dialog')).queryByRole('combobox',{name:'G2P 模型'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'保存配套'})); await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(rpc).toHaveBeenLastCalledWith('mfa.save',expect.objectContaining({preset:expect.objectContaining({g2p:''})}));
  expect((screen.getByRole('combobox',{name:'G2P 模型'}) as HTMLSelectElement).value).toBe('');
});
it('uses the same generation and submission interaction for a custom language', async () => {
  const custom = {...mfaFixture.presets[1],id:'custom-'+'a'.repeat(32),name:'测试德语',language:'de',rule_id:null};
  const mfa = {...mfaFixture,presets:[...mfaFixture.presets,custom]};
  const generated = {...generationFixture,preset_id:custom.id,transcript:'Straße Straße',words:[{word:'straße',candidates:[{raw:['a'],ipa:['a'],source:'MFA G2P'}]}],sequence:['straße','straße']};
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? {...data,mfa} : method === 'tutor.generate_phonemes' ? generated : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await screen.findByRole('combobox',{name:'目标语言与读音'});
  fireEvent.change(screen.getByRole('combobox',{name:'目标语言与读音'}),{target:{value:custom.id}});
  fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'Straße Straße'}});
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'})); await waitFor(() => expect(ipa().value).toBe('a a'));
  fireEvent.click(start());
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit',expect.objectContaining({preset_id:custom.id,language:'de',choices:{straße:0},generation_id:'generation-1',phonemes:'a a'})));
});
it('accepts server-normalized surrounding whitespace without treating the result as stale', async () => {
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? data : generationFixture);
  render(<PronunciationForm rpc={rpc}/>); await selectUS();
  fireEvent.change(screen.getByRole('textbox',{name:/参考文本/}),{target:{value:'  hello hello  '}});
  fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  await waitFor(() => expect(start().disabled).toBe(false)); expect(ipa().value).toContain('h ə');
});
it('allows G2P alone but blocks generated analysis when the bound dictionary is missing', async () => {
  const missing = {...data,mfa:{...mfaFixture,presets:mfaFixture.presets.map(p => p.id === 'en-US' ? {...p,dictionary:''} : p)}};
  const rpc = vi.fn(async (method: string) => method === 'tutor.bootstrap' ? missing : generationFixture);
  render(<PronunciationForm rpc={rpc}/>); await selectUS(); fireEvent.click(screen.getByRole('button',{name:'自动生成音位'}));
  await waitFor(() => expect(ipa().value).toContain('h ə')); expect(start().disabled).toBe(true);
});
