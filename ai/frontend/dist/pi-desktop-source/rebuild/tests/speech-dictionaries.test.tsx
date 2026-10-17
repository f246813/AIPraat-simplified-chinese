import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SpeechDictionaries, type DictionaryResult } from '../src/SpeechDictionaries';
import { mfaFixture } from './mfa-fixtures';

const data: DictionaryResult = {dictionaries: [{name: '中文.dict', path: 'C:/词典/中文.dict', exists: true, active: true}], theme: 'system'};
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
  HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); };
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('opens the Wav2Vec2 folder picker and selects a model through its own namespace', async () => {
  const first = {name: '中文模型', path: 'C:/Wav2Vec2/中文模型', exists: true, active: true};
  const second = {name: '英语模型', path: 'C:/Wav2Vec2/英语模型', exists: true, active: false};
  const rpc = vi.fn().mockResolvedValueOnce(data)
    .mockResolvedValueOnce({models: [first], theme: 'light'})
    .mockResolvedValueOnce({models: [first, second], theme: 'light'})
    .mockResolvedValueOnce({models: [{...first, active: false}, {...second, active: true}], theme: 'light'});
  render(<SpeechDictionaries rpc={rpc}/>);
  await screen.findByText('中文.dict');
  fireEvent.click(screen.getByRole('button', {name: 'Wav2Vec2模型'}));
  await screen.findByText(first.path);
  expect(rpc).toHaveBeenLastCalledWith('wav2vec2_models.list', {});
  expect(screen.getByRole('heading', {name: '管理Wav2Vec2声学模型'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: '添加Wav2Vec2声学模型'}));
  const row = await screen.findByRole('button', {name: /英语模型/});
  expect(rpc).toHaveBeenLastCalledWith('wav2vec2_models.choose', {});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '选用该模型'}));
  await waitFor(() => expect(row.classList.contains('is-active')).toBe(true));
  expect(rpc).toHaveBeenLastCalledWith('wav2vec2_models.select', {path: second.path});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '删除'}));
  expect(screen.getByText(/模型目录会保留在磁盘上/)).toBeTruthy();
});

it('loads configured paths and opens the native picker from the bottom action', async () => {
  const rpc = vi.fn().mockResolvedValue(data);
  render(<SpeechDictionaries rpc={rpc}/>);
  await screen.findByText('C:/词典/中文.dict');
  fireEvent.click(screen.getByRole('button', {name: '添加MFA语音词典'}));
  await waitFor(() => expect(rpc).toHaveBeenCalledWith('dictionaries.choose', {}));
  expect(screen.getByText('中文.dict')).toBeTruthy();
});

it('right-click deletion requires choosing delete; cancel leaves configuration intact', async () => {
  const rpc = vi.fn().mockResolvedValue(data);
  render(<SpeechDictionaries rpc={rpc}/>);
  const row = await screen.findByRole('button', {name: /中文.dict/});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '删除'}));
  fireEvent.click(screen.getByRole('button', {name: '取消'}));
  expect(rpc).toHaveBeenCalledTimes(2); expect(rpc).toHaveBeenLastCalledWith('mfa.list',{});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '删除'}));
  rpc.mockResolvedValue({dictionaries: [], theme: 'system'});
  await waitFor(() => expect((screen.getByRole('button',{name:'删除'}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', {name: '删除'}));
  await screen.findByText('尚未配置MFA语音词典');
  expect(rpc).toHaveBeenLastCalledWith('dictionaries.remove', {path: data.dictionaries[0].path});
});

it('shows picker errors and keeps the existing list', async () => {
  const rpc = vi.fn().mockResolvedValueOnce(data).mockRejectedValueOnce(new Error('文件不可读'));
  render(<SpeechDictionaries rpc={rpc}/>);
  await screen.findByText('中文.dict');
  fireEvent.click(screen.getByRole('button', {name: '添加MFA语音词典'}));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.getByText('中文.dict')).toBeTruthy();
});

it('left-clicking the select menu updates the current check and border after host save', async () => {
  const second = {name: '英语.dict', path: 'C:/词典/英语.dict', exists: true, active: false};
  const initial = {...data, dictionaries: [...data.dictionaries, second]};
  const selected = {...data, dictionaries: [{...data.dictionaries[0], active: false}, {...second, active: true}]};
  const rpc = vi.fn().mockResolvedValueOnce(initial).mockResolvedValueOnce(selected);
  render(<SpeechDictionaries rpc={rpc}/>);
  const row = await screen.findByRole('button', {name: /英语.dict/});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '选用该词典'}));
  await waitFor(() => expect(row.classList.contains('is-active')).toBe(true));
  expect(rpc).toHaveBeenLastCalledWith('dictionaries.select', {path: second.path});
  expect(row.getAttribute('aria-current')).toBe('true');
  expect(row.querySelector('.dictionary-current svg')).toBeTruthy();
  expect(document.querySelectorAll('.dictionary-row.is-active')).toHaveLength(1);
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  expect((screen.getByRole('menuitem', {name: '选用该词典'}) as HTMLButtonElement).disabled).toBe(true);
});

it('keeps the old active dictionary when selecting fails', async () => {
  const second = {name: '英语.dict', path: 'C:/词典/英语.dict', exists: true, active: false};
  const rpc = vi.fn().mockResolvedValueOnce({...data, dictionaries: [...data.dictionaries, second]}).mockRejectedValueOnce(new Error('无法保存配置'));
  render(<SpeechDictionaries rpc={rpc}/>);
  const row = await screen.findByRole('button', {name: /英语.dict/});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '选用该词典'}));
  await screen.findByRole('alert');
  expect(document.querySelector('.dictionary-row.is-active')?.textContent).toContain('中文.dict');
  expect(row.classList.contains('is-active')).toBe(false);
});

it('does not offer selection of a missing dictionary file', async () => {
  const rpc = vi.fn().mockResolvedValue({...data, dictionaries: [{...data.dictionaries[0], active: false, exists: false}]});
  render(<SpeechDictionaries rpc={rpc}/>);
  const row = await screen.findByRole('button', {name: /中文.dict/});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  expect((screen.getByRole('menuitem', {name: '选用该词典'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('menuitem', {name: '删除'}) as HTMLButtonElement).disabled).toBe(false);
});

it('switches the sidebar to acoustic models and uses their picker and current selection', async () => {
  const first = {name: '中文声学.zip', path: 'C:/模型/中文声学.zip', exists: true, active: true};
  const second = {name: '英语声学.zip', path: 'C:/模型/英语声学.zip', exists: true, active: false};
  const rpc = vi.fn().mockResolvedValueOnce(data)
    .mockResolvedValueOnce({models: [first, second], theme: 'system'})
    .mockResolvedValueOnce({models: [first, second], theme: 'system'})
    .mockResolvedValueOnce({models: [{...first, active: false}, {...second, active: true}], theme: 'system'});
  render(<SpeechDictionaries rpc={rpc}/>);
  await screen.findByText('中文.dict');
  expect(screen.getByRole('heading', {name: '管理MFA语音词典'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'MFA声学模型'}));
  const row = await screen.findByRole('button', {name: /英语声学.zip/});
  expect(screen.queryByText('中文.dict')).toBeNull();
  expect(rpc).toHaveBeenLastCalledWith('acoustic_models.list', {});
  fireEvent.click(screen.getByRole('button', {name: '添加MFA声学模型'}));
  await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('acoustic_models.choose', {}));
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '选用该模型'}));
  await waitFor(() => expect(row.classList.contains('is-active')).toBe(true));
  expect(rpc).toHaveBeenLastCalledWith('acoustic_models.select', {path: second.path});
  expect(document.querySelectorAll('.dictionary-row.is-active')).toHaveLength(1);
});

it('models share deletion confirmation and return to dictionaries through the sidebar', async () => {
  const model = {name: '中文声学.zip', path: 'C:/模型/中文声学.zip', exists: true, active: true};
  const rpc = vi.fn().mockResolvedValueOnce(data).mockResolvedValue({models: [model], theme: 'dark'});
  render(<SpeechDictionaries rpc={rpc}/>);
  await screen.findByText('中文.dict');
  fireEvent.click(screen.getByRole('button', {name: 'MFA声学模型'}));
  const row = await screen.findByRole('button', {name: /中文声学.zip/});
  expect(document.documentElement.dataset.theme).toBe('dark');
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '删除'}));
  expect(screen.getByRole('dialog', {name: '删除MFA声学模型'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: '取消'}));
  expect(rpc).toHaveBeenCalledTimes(3); expect(rpc).toHaveBeenLastCalledWith('mfa.list',{});
  fireEvent.contextMenu(row, {clientX: 80, clientY: 100});
  fireEvent.click(screen.getByRole('menuitem', {name: '删除'}));
  rpc.mockResolvedValue({models: [], theme: 'dark'});
  await waitFor(() => expect((screen.getByRole('button',{name:'删除'}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', {name: '删除'}));
  await screen.findByText('尚未配置MFA声学模型');
  expect(rpc).toHaveBeenLastCalledWith('acoustic_models.remove', {path: model.path});
  rpc.mockResolvedValue(data);
  fireEvent.click(screen.getByRole('button', {name: 'MFA语音词典'}));
  await screen.findByText('中文.dict');
  expect(rpc).toHaveBeenLastCalledWith('dictionaries.list', {});
});

it('registers G2P resources in their own namespace and warns about referenced presets before removal', async () => {
  const rpc = vi.fn(async (method: string) => method.startsWith('mfa.') ? mfaFixture : data);
  render(<SpeechDictionaries rpc={rpc}/>); await screen.findByText('中文.dict');
  fireEvent.click(screen.getByRole('button',{name:'MFA G2P 模型'})); await screen.findByText('English G2P.zip');
  expect(rpc).toHaveBeenLastCalledWith('mfa.list',{});
  fireEvent.click(screen.getByRole('button',{name:'添加 MFA G2P 模型'})); await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('mfa.choose',{kind:'g2p'}));
  await waitFor(() => expect((screen.getByRole('button',{name:'移除 English G2P.zip 登记'}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'移除 English G2P.zip 登记'}));
  expect(screen.getByText(/受影响预设：英语（美式）/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'移除登记'})); await waitFor(() => expect(rpc).toHaveBeenLastCalledWith('mfa.remove',{kind:'g2p',id:'g'}));
  expect(rpc.mock.calls.some(([method]) => method === 'dictionaries.select')).toBe(false);
});
it('opens the same editor from resource pairing entry without changing legacy active configuration', async () => {
  const rpc = vi.fn(async (method: string) => method === 'mfa.list' ? mfaFixture : data);
  render(<SpeechDictionaries rpc={rpc}/>); await screen.findByText('中文.dict');
  fireEvent.click(screen.getByRole('button',{name:'纠音资源配套'}));
  fireEvent.click(await screen.findByRole('button',{name:'配置 英语（英式） 配套资源'}));
  expect(screen.getByRole('dialog',{name:'配置配套资源 · 英语（英式）'})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'取消'})); expect(screen.queryByRole('dialog')).toBeNull(); expect(rpc).toHaveBeenCalledTimes(2);
});

it.each(['dictionaries','acoustic_models'])('warns about exact referenced preset names before legacy %s deletion', async category => {
  const kind = category === 'dictionaries' ? 'dictionary' : 'acoustic'; const resource = mfaFixture.resources[kind][0];
  const rpc = vi.fn(async (method: string) => method === 'mfa.list' ? mfaFixture : {dictionaries:[{...resource,active:true}],models:[{...resource,active:true}],theme:'system'});
  render(<SpeechDictionaries rpc={rpc}/>); await screen.findByText(resource.name);
  if (category === 'acoustic_models') {fireEvent.click(screen.getByRole('button',{name:'MFA声学模型'})); await screen.findByRole('heading',{name:'管理MFA声学模型'});}
  fireEvent.contextMenu(screen.getByRole('button',{name:new RegExp(resource.name)})); fireEvent.click(screen.getByRole('menuitem',{name:'删除'}));
  await screen.findByText(/受影响纠音预设：英语（美式）/);
  expect(rpc).toHaveBeenLastCalledWith('mfa.list',{}); expect(screen.getByText(/不会自动替换绑定/)).toBeTruthy();
});
