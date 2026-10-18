import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {PronunciationForm} from '../src/PronunciationForm';
import type {EnvironmentReport} from '../src/EnvironmentStatus';

beforeEach(() => {vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})); HTMLDialogElement.prototype.showModal = function() {this.setAttribute('open', '');};});
afterEach(cleanup);

const data = {
  sounds: [{id: '1', name: '标准音.wav'}, {id: '2', name: '学习者.wav'}],
  profiles: [{id: 'en-US', name: '美式英语', language: 'en'}],
  model: {source: '云端 API', name: 'cloud-fixture'},
  theme: 'light',
};

const blocked: EnvironmentReport = {
  ready: false, degraded: false, missing: ['Praat 可执行文件'],
  message: '缺少必需运行件：Praat 可执行文件。',
  items: [{id: 'praat', label: 'Praat 可执行文件', required: true, ready: false,
           detail: '未找到', hint: '把 Praat.exe 放到安装目录（或设置 PRAAT_AI_PRAAT_EXECUTABLE），再点“重新检测”。'}],
};

const degraded: EnvironmentReport = {
  ready: true, degraded: true, missing: [],
  message: '可选项缺失（会降级，不影响主流程）：ffmpeg（m4a 等压缩音频解码）。',
  items: [
    {id: 'praat', label: 'Praat 可执行文件', required: true, ready: true, detail: 'D:/AIPraat/Praat.exe', hint: ''},
    {id: 'ffmpeg', label: 'ffmpeg（m4a 等压缩音频解码）', required: false, ready: false,
     detail: '未找到', hint: '运行 ai/Install-FFmpeg.ps1'},
  ],
};

describe('运行环境面板', () => {
  it('必需件缺失时说明原因并挡住提交', async () => {
    const rpc = vi.fn().mockResolvedValue({...data, environment: blocked});
    render(<PronunciationForm rpc={rpc}/>);
    await screen.findAllByText(/缺少必需运行件/);
    expect((screen.getByRole('button', {name: /开始分析/}) as HTMLButtonElement).disabled).toBe(true);
    // 只有一次 RPC：环境随 bootstrap 一起下发，不为面板多发请求
    expect(rpc).toHaveBeenCalledTimes(1);
  });

  it('可选项缺失只提示降级，不禁用提交', async () => {
    const rpc = vi.fn().mockResolvedValue({...data, environment: degraded});
    render(<PronunciationForm rpc={rpc}/>);
    await screen.findByText(/可选项缺失/);
    expect(screen.getByText('D:/AIPraat/Praat.exe')).toBeTruthy();
    expect((screen.getByRole('button', {name: /开始分析/}) as HTMLButtonElement).disabled).toBe(false);
  });

  it('重新检测走后端刷新 RPC 并更新面板', async () => {
    const rpc = vi.fn(async (method: string) => method === 'tutor.refresh_environment'
      ? {...degraded, degraded: false, message: '', items: degraded.items.map(item => ({...item, ready: true, detail: 'ok'}))}
      : {...data, environment: degraded});
    render(<PronunciationForm rpc={rpc}/>);
    await screen.findByText(/可选项缺失/);
    await fireEvent.click(screen.getByRole('button', {name: '重新检测'}));
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('tutor.refresh_environment', {}));
    await waitFor(() => expect(screen.queryByText(/可选项缺失/)).toBeNull());
  });
});
