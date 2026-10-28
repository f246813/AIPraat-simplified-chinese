import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {PronunciationForm} from '../src/PronunciationForm';
import {bootstrapFixture} from './mfa-fixtures';
const bootstrap = {...bootstrapFixture, normalization: {version:'normalization-v1', formants:false, pitch:false, loudness:false, inventories:{'en-US-general-v1':['i','ɪ','ɛ','æ','ɑ','ɔ','ʊ','u','ʌ']}}};
beforeEach(() => vi.stubGlobal('matchMedia', () => ({matches:false, addEventListener:vi.fn(), removeEventListener:vi.fn()})));
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const prepare = async () => {await screen.findByRole('combobox', {name:'目标语言与读音'}); fireEvent.click(screen.getByRole('radio', {name:/音位判据/})); fireEvent.change(screen.getByRole('textbox', {name:/目标音位/}), {target:{value:'a'}});};

it('keeps both independent switches off by default and submits explicit utterance pitch confirmation', async () => {
  const rpc = vi.fn(async (method:string, _params:Record<string,unknown>) => method === 'tutor.bootstrap' ? bootstrap : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  expect((screen.getByRole('checkbox', {name:/相对音高（半音/}) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('checkbox', {name:/整段恒定响度/}) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByRole('checkbox', {name:/相对音高（半音/}));
  fireEvent.click(screen.getByRole('checkbox', {name:/整段恒定响度/}));
  expect(screen.getByText(/400 ms/)).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', {name:/已检查 F0/}));
  expect(screen.getByText(/使用本句相对半音/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name:'开始分析'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.submit', expect.objectContaining({normalization:expect.objectContaining({pitch:true,loudness:true,pitch_confirmed:true,formants:false})})));
});

it('imports neutral baselines via receipts, clears them and confirmation after range/speaker changes', async () => {
  const rpc = vi.fn(async (method:string, params:Record<string,unknown>) => method === 'tutor.bootstrap' ? bootstrap : {id:params.role,baseline:{status:'applied',reason:'',baseline_hz:125,material:{wav_name:String(params.role)+'.wav'}}});
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  fireEvent.click(screen.getByRole('checkbox', {name:/相对音高（半音/}));
  fireEvent.change(screen.getByRole('combobox', {name:'音高基线方式'}), {target:{value:'independent'}});
  for (const name of ['标准说话人中性语句','学习者中性语句']) fireEvent.click(within(screen.getByText(name).parentElement!).getByRole('button'));
  // Imports are serialized by the disabled fieldset, so await each slot explicitly.
  await screen.findByText(/reference.wav/);
  fireEvent.click(within(screen.getByText('学习者中性语句').parentElement!).getByRole('button'));
  await screen.findByText(/learner.wav/);
  fireEvent.click(screen.getByRole('checkbox', {name:/已检查 F0/}));
  expect(screen.getByText(/使用双方独立中性语句/)).toBeTruthy();
  fireEvent.change(screen.getByRole('spinbutton', {name:'F0 下限（Hz）'}), {target:{value:'90'}});
  expect(screen.queryByText(/reference.wav/)).toBeNull();
  expect((screen.getByRole('checkbox', {name:/已检查 F0/}) as HTMLInputElement).checked).toBe(false);
  fireEvent.change(screen.getByRole('combobox', {name:'学习者录音'}), {target:{value:'4'}});
  expect((screen.getByRole('combobox', {name:'音高基线方式'}) as HTMLSelectElement).value).toBe('utterance');
});

it('restores enabled preferences but never restores pitch confirmation or calibration identity', async () => {
  const rpc = vi.fn(async () => ({...bootstrap,normalization:{...bootstrap.normalization,pitch:true,loudness:true}}));
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  expect((screen.getByRole('checkbox', {name:/相对音高（半音/}) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole('checkbox', {name:/已检查 F0/}) as HTMLInputElement).checked).toBe(false);
  expect(screen.getByText(/请检查 F0 范围并确认音高基线/)).toBeTruthy();
});

it('previews the actual candidate method from selected WAV durations and recomputes after changing objects', async () => {
  const rpc = vi.fn(async () => ({...bootstrap, sounds:bootstrap.sounds.map((sound,index) => ({...sound,duration:index === 0 ? .2 : 1}))}));
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  fireEvent.click(screen.getByRole('checkbox', {name:/整段恒定响度/}));
  expect(screen.getByText(/任一侧短于 400 ms，双方使用可靠活动区间的 RMS/)).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox', {name:'标准音范本'}), {target:{value:'9'}});
  expect(screen.getByText(/双方均至少 400 ms/)).toBeTruthy();
});

it('uses existing runtime configuration RPC, not an automatic install call', async () => {
  const rpc = vi.fn(async (method:string) => method === 'tutor.bootstrap' ? bootstrap : {opened:true,message:'已打开路径配置'});
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  fireEvent.click(screen.getByRole('button', {name:'路径配置 → 配置运行库…'}));
  await screen.findByText('已打开路径配置');
  expect(rpc).toHaveBeenCalledWith('tutor.configure_runtime', {});
  expect(rpc.mock.calls.some(([method]) => method.includes('install'))).toBe(false);
});

it('gates loudness and pitch on the reported runtime capabilities before submitting', async () => {
  const capabilities = {ready:false, features:{
    loudness:{label:'整段恒定响度（LUFS）', ready:false, reason:'版本不匹配：需要 scipy>=1.14.1,<1.15，当前 1.15.0', hint:'请在“路径配置 → 配置运行库…”安装；装好后关闭并重新打开纠音窗口。'},
    pitch:{label:'基频提取与相对音高（半音）', ready:false, reason:'未安装/无法加载 praat-parselmouth（ModuleNotFoundError）', hint:'请在“路径配置 → 配置运行库…”安装；装好后关闭并重新打开纠音窗口。'}}};
  const rpc = vi.fn(async (method:string) => method === 'tutor.bootstrap' ? bootstrap : method === 'tutor.capabilities' ? capabilities : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  const loudness = screen.getByRole('checkbox', {name:/整段恒定响度/}) as HTMLInputElement;
  const pitch = screen.getByRole('checkbox', {name:/相对音高（半音/}) as HTMLInputElement;
  await waitFor(() => expect(loudness.disabled).toBe(true));
  expect(pitch.disabled).toBe(true);
  expect(screen.getByText(/缺少响度运行库/)).toBeTruthy();
  // 提示必须指名缺什么、去哪儿装，而不是只说「失败」。
  expect(screen.getByText(/scipy>=1\.14\.1,<1\.15/)).toBeTruthy();
  expect(screen.getByText(/praat-parselmouth/)).toBeTruthy();
  const offline = screen.getAllByText(/路径配置 → 配置运行库…/).filter(node => node.getAttribute('role') === 'alert');
  expect(offline.length).toBe(2);  // 音高与响度各一条，都指向同一个入口
  expect(rpc).toHaveBeenCalledWith('tutor.capabilities', {});
});

it('clears a restored loudness preference that this interpreter cannot run', async () => {
  const capabilities = {ready:false, features:{loudness:{label:'整段恒定响度（LUFS）', ready:false, reason:'未安装/无法加载 pyloudnorm（ModuleNotFoundError）', hint:'请在“路径配置 → 配置运行库…”安装；装好后关闭并重新打开纠音窗口。'}}};
  const restored = {...bootstrap, normalization:{...bootstrap.normalization, loudness:true}};
  const rpc = vi.fn(async (method:string) => method === 'tutor.bootstrap' ? restored : method === 'tutor.capabilities' ? capabilities : {accepted:true});
  render(<PronunciationForm rpc={rpc}/>); await prepare();
  const loudness = screen.getByRole('checkbox', {name:/整段恒定响度/}) as HTMLInputElement;
  await waitFor(() => expect(loudness.checked).toBe(false));
  expect(loudness.disabled).toBe(true);
  expect(screen.getByRole('alert').textContent).toMatch(/pyloudnorm/);
});
