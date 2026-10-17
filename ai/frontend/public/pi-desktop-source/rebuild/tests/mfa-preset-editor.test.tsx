import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MfaPresetEditor } from '../src/MfaPresetEditor';
import { mfaFixture, environmentFixture, readyStatus } from './mfa-fixtures';

beforeEach(() => {HTMLDialogElement.prototype.showModal = function() {this.setAttribute('open','');};});
afterEach(cleanup);
const setup = (rpc = vi.fn().mockResolvedValue(mfaFixture)) => {
  const onData = vi.fn(); const onClose = vi.fn();
  render(<MfaPresetEditor data={mfaFixture} presetId="en-GB" rpc={rpc} onData={onData} onClose={onClose}/>);
  return {rpc,onData,onClose};
};
it('keeps names immutable and imports persistent resources separately from unsaved bindings', async () => {
  const {rpc,onData,onClose} = setup();
  expect(screen.queryByRole('textbox',{name:/名称/})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'添加本地G2P模型'}));
  await waitFor(() => expect(onData).toHaveBeenCalledWith(mfaFixture));
  expect(rpc).toHaveBeenLastCalledWith('mfa.choose',{kind:'g2p'});
  expect((screen.getByRole('combobox',{name:'G2P 模型'}) as HTMLSelectElement).value).toBe('');
  fireEvent.change(screen.getByRole('combobox',{name:'G2P 模型'}),{target:{value:'g'}});
  fireEvent.click(screen.getByRole('button',{name:'取消'})); expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('alert').textContent).toContain('已导入资源仍保留');
  fireEvent.click(screen.getByRole('button',{name:'继续编辑'})); expect(onClose).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'关闭资源配置'})); fireEvent.click(screen.getByRole('button',{name:'丢弃并关闭'}));
  expect(onClose).toHaveBeenCalledOnce(); expect(rpc).toHaveBeenCalledTimes(1);
});
it('prevents Escape dismissal with dirty state and closes clean draft without saving', () => {
  const {onClose,rpc} = setup(); fireEvent.change(screen.getByRole('textbox',{name:'MFA 可执行文件路径'}),{target:{value:'C:/new mfa.exe'}});
  const event = new Event('cancel',{cancelable:true,bubbles:true}); fireEvent(screen.getByRole('dialog'),event);
  expect(event.defaultPrevented).toBe(true); expect(onClose).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'继续编辑'}));
  fireEvent.change(screen.getByRole('textbox',{name:'MFA 可执行文件路径'}),{target:{value:mfaFixture.launcher.executable}});
  fireEvent.click(screen.getByRole('button',{name:'取消'})); expect(onClose).toHaveBeenCalledOnce(); expect(rpc).not.toHaveBeenCalled();
});
it('assigns launcher picker to the caller-chosen field and checks launcher with exact RPC parameters', async () => {
  const rpc = vi.fn().mockResolvedValueOnce({path:'C:/中文路径/conda.exe'}).mockResolvedValueOnce({version:'3.3',help_order:'INPUT MODEL OUTPUT',status:'ok'});
  setup(rpc); fireEvent.click(screen.getByRole('button',{name:'选择Conda路径'}));
  await waitFor(() => expect((screen.getByRole('textbox',{name:'Conda 可执行文件路径'}) as HTMLInputElement).value).toBe('C:/中文路径/conda.exe'));
  expect(rpc).toHaveBeenLastCalledWith('mfa.choose_launcher',{});
  expect((screen.getByRole('textbox',{name:'MFA 可执行文件路径'}) as HTMLInputElement).value).toBe(mfaFixture.launcher.executable);
  fireEvent.change(screen.getByRole('textbox',{name:'MFA 可执行文件路径'}),{target:{value:''}});
  fireEvent.change(screen.getByRole('textbox',{name:'Conda 环境名'}),{target:{value:'mfa'}});
  fireEvent.click(screen.getByRole('button',{name:'检查 MFA'})); await screen.findByText(/版本：3.3/);
  expect(rpc).toHaveBeenLastCalledWith('mfa.check_launcher',{launcher:{executable:'',conda_executable:'C:/中文路径/conda.exe',conda_environment:'mfa'}});
});
it('detects the alignment environment with the draft preset and launcher, then re-detects on demand', async () => {
  const rpc = vi.fn().mockResolvedValueOnce(environmentFixture).mockResolvedValueOnce({...environmentFixture, ready:true, summary:'对齐环境可用（conda run -n aligner）'});
  setup(rpc);
  fireEvent.change(screen.getByRole('textbox',{name:'Conda 环境名'}),{target:{value:'aligner'}});
  fireEvent.click(screen.getByRole('button',{name:'检测对齐环境'}));
  await screen.findByText(/对齐环境不完整（2 项）/);
  expect(rpc).toHaveBeenLastCalledWith('mfa.environment',{preset:expect.objectContaining({id:'en-GB'}),launcher:{executable:'C:/MFA/mfa.exe',conda_executable:'',conda_environment:'aligner'}});
  expect(screen.getByRole('alert').textContent).toContain('首个阻塞：libsndfile 音频读写：cannot load library');
  expect(screen.getByText(/libsndfile 音频读写：阻塞/)).toBeTruthy();
  expect(screen.getByText(/修复：改用 conda run/)).toBeTruthy();
  expect(screen.getByText(/词典：English\.dict/)).toBeTruthy();
  expect(screen.getByText(/不会安装、下载或修改任何配置/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'重新检测'}));
  await screen.findByText(/对齐环境可用（conda run -n aligner）/);
  expect(rpc).toHaveBeenCalledTimes(2);
});
it('drops a stale environment report when the draft changes and never detects with an invalid mapping', async () => {
  const rpc = vi.fn().mockResolvedValue(environmentFixture);
  setup(rpc);
  fireEvent.click(screen.getByRole('button',{name:'检测对齐环境'}));
  await screen.findByText(/对齐环境不完整（2 项）/);
  fireEvent.change(screen.getByRole('textbox',{name:'MFA 可执行文件路径'}),{target:{value:'C:/other mfa.exe'}});
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('启动配置尚未保存；请检查 MFA。')).toBeTruthy();
  fireEvent.click(screen.getByText('音位体系与高级 IPA 映射'));
  fireEvent.change(screen.getByRole('combobox',{name:'音位体系'}),{target:{value:'custom'}});
  fireEvent.change(screen.getByRole('textbox',{name:'原始标签 → IPA 映射（JSON）'}),{target:{value:'[]'}});
  fireEvent.click(screen.getByRole('button',{name:'检测对齐环境'}));
  await screen.findByRole('alert');
  expect(rpc).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText('对齐环境检测结果')).toBeNull();
});
it('validates custom JSON and saves partial configurations using the complete immutable preset contract', async () => {
  const {rpc,onClose} = setup(); fireEvent.click(screen.getByText('音位体系与高级 IPA 映射'));
  fireEvent.change(screen.getByRole('combobox',{name:'音位体系'}),{target:{value:'custom'}});
  const mapping = screen.getByRole('textbox',{name:'原始标签 → IPA 映射（JSON）'});
  fireEvent.change(mapping,{target:{value:'[]'}}); fireEvent.click(screen.getByRole('button',{name:'保存配套'}));
  await screen.findByRole('alert'); expect(rpc).not.toHaveBeenCalled();
  fireEvent.change(mapping,{target:{value:'{"X":"a b"}'}}); fireEvent.click(screen.getByRole('button',{name:'检查兼容性'}));
  await waitFor(() => expect((screen.getByRole('button',{name:'保存配套'}) as HTMLButtonElement).disabled).toBe(false)); expect(rpc).not.toHaveBeenCalled();
  fireEvent.change(mapping,{target:{value:'{"X":"t͡ʃ"}'}});
  fireEvent.click(screen.getByRole('checkbox',{name:/我确认/})); fireEvent.click(screen.getByRole('checkbox',{name:/默认预设/}));
  fireEvent.click(screen.getByRole('button',{name:'保存配套'})); await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(rpc).toHaveBeenLastCalledWith('mfa.save',{preset:expect.objectContaining({id:'en-GB',name:'英语（英式）',language:'en',variant:'GB',rule_id:null,g2p:'',dictionary:'',acoustic:'',phone_set:'custom',phone_map:{X:'t͡ʃ'},confirmed:true}),launcher:mfaFixture.launcher,make_default:true});
});
it('shows compatibility issues and save rejection without claiming real model verification or losing the draft', async () => {
  const rpc = vi.fn().mockResolvedValueOnce({...readyStatus,compatibility:'conflict',issues:['G2P 标签不被声学模型覆盖，请换兼容模型。'],can_generate:false}).mockRejectedValueOnce(new Error('不能保存已知冲突'));
  const {onClose} = setup(rpc); fireEvent.change(screen.getByRole('combobox',{name:'G2P 模型'}),{target:{value:'g'}});
  fireEvent.click(screen.getByRole('button',{name:'检查兼容性'})); await screen.findByText('G2P 标签不被声学模型覆盖，请换兼容模型。');
  expect(rpc).toHaveBeenLastCalledWith('mfa.check',{preset:expect.objectContaining({id:'en-GB',g2p:'g'}),launcher:mfaFixture.launcher});
  fireEvent.click(screen.getByRole('button',{name:'保存配套'})); await screen.findByText('不能保存已知冲突');
  expect(onClose).not.toHaveBeenCalled(); expect((screen.getByRole('combobox',{name:'G2P 模型'}) as HTMLSelectElement).value).toBe('g');
});
