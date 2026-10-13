import { useEffect, useState } from 'react';
import { HostBridge } from './bridge';
import './anki-integration.css';

type Settings = {media_dir: string; ffmpeg: string; fallback: boolean; audio_field: string; start_field: string; end_field: string; mode: string; magnifier_selector: string};
type Result = {settings?: Settings; code?: string; path?: string; message?: string};
export type AnkiRpc = (method: string, params: Record<string, unknown>) => Promise<Result>;
const hostRpc: AnkiRpc = async (method, params) => {
  await new HostBridge().wait();
  return await window.pywebview!.api!.rpc!(method, params) as Result;
};

export function AnkiIntegration({rpc=hostRpc}: {rpc?: AnkiRpc}) {
  const [settings,setSettings] = useState<Settings>();
  const [code,setCode] = useState(''); const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState(''); const [error,setError] = useState('');
  const [audio,setAudio] = useState(''); const [start,setStart] = useState(''); const [end,setEnd] = useState('');
  const run = async (method: string, params: Record<string, unknown>={}) => {
    setBusy(true); setError(''); setMessage('');
    try { const result = await rpc(method,params); if(result.settings) setSettings(result.settings); if(result.code) setCode(result.code); if(result.message) setMessage(result.message); return result; }
    catch(e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  };
  useEffect(()=>{void run('anki.settings'); const media=matchMedia('(prefers-color-scheme: dark)'); const theme=()=>{document.documentElement.dataset.theme=media.matches?'dark':'light';}; theme(); media.addEventListener('change',theme); return ()=>media.removeEventListener('change',theme);},[rpc]);
  const change = (key: keyof Settings,value: string|boolean) => setSettings(s=>s?{...s,[key]:value}:s);
  const choose = async (key: string) => {const result=await run('anki.choose',{key}); if(result?.path) {if(key==='test_audio') setAudio(result.path); else change(key as keyof Settings,result.path);}};
  const exportFile = async (method: string) => {if(settings && await run('anki.save',settings)) await run(method);};
  return <main className="anki-integration">
    <header><h1>Anki 连接</h1><p className="muted">把卡片里的声音一键打开到 AIPraat 语图视窗。</p></header>
    {error && <div className="warning" role="alert">{error}</div>}{message && <div className="anki-result" role="status">{message}</div>}
    {!settings ? <p>正在读取连接设置…</p> : <>
    <section className="anki-entry"><h2>卡片入口</h2><div className="anki-modes">{[['button','新增“查看语图”按钮'],['magnifier','复用原放大镜'],['both','保留两个入口']].map(([value,label])=><label key={value}><input type="radio" name="entry" checked={settings.mode===value} disabled={busy} onChange={()=>change('mode',value)}/>{label}</label>)}</div>
      {settings.mode!=='button' && <label className="field"><span>放大镜选择器</span><input value={settings.magnifier_selector} onChange={e=>change('magnifier_selector',e.target.value)}/><small className="muted">例如按钮 class="search-link" 时填 .search-link。只替换 Windows 上的点击行为。</small></label>}
      <div className="actions"><button className="primary" disabled={busy} onClick={()=>void exportFile('anki.export')}>导出 Anki 插件</button><button disabled={busy} onClick={()=>void run('anki.save',settings)}>保存设置</button></div>
      <p className="muted">在 Anki 中选择“工具 → 插件 → 从文件安装”，安装导出的 .ankiaddon 后重启。多音频卡片会让你选择对应声音。</p>
    </section>
    <section><h2>音频与定位</h2><label className="field"><span>卡片音频字段（可选）</span><input placeholder="留空时读取卡片正反面的音频" value={settings.audio_field} onChange={e=>change('audio_field',e.target.value)}/></label>
      <div className="anki-grid">{[['start_field','开始秒数字段'],['end_field','结束秒数字段']].map(([key,label])=><label className="field" key={key}><span>{label}（可选）</span><input value={settings[key as 'start_field'|'end_field']} onChange={e=>change(key as keyof Settings,e.target.value)}/></label>)}</div>
      <p className="muted">音频统一先尝试原生读取，明确失败后使用 FFmpeg 解码为临时 WAV。请在“路径配置”的 Python 环境下方配置、检测或一键安装 FFmpeg。临时 WAV 在读取完成后清理，原音频保持完整。</p>
    </section>
    <section><h2>打开测试</h2><div className="anki-path"><input aria-label="测试音频路径" placeholder="选择 WAV、MP3、M4A 等声音文件" value={audio} onChange={e=>setAudio(e.target.value)}/><button disabled={busy} onClick={()=>void choose('test_audio')}>浏览…</button></div><div className="anki-grid"><label className="field"><span>开始（秒，可选）</span><input value={start} onChange={e=>setStart(e.target.value)}/></label><label className="field"><span>结束（秒，可选）</span><input value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
      <button disabled={busy||!audio} onClick={()=>void (async()=>{if(await run('anki.save',settings)) await run('anki.open',{audio,start,end});})()}>{busy?'正在处理…':'打开到语图视窗'}</button>
    </section>
    <section><h2>用 VS Code 修改入口</h2><p className="muted">在 Anki 插件管理中点击“查看文件”，打开 entry.js。把 button 改成 magnifier 即可切换；自定义卡组再修改下一行按钮选择器。重新显示卡片后生效。</p><pre>{code.split('\n').slice(0,5).join('\n')}</pre><button disabled={busy} onClick={()=>void exportFile('anki.code')}>导出可编辑的 entry.js</button></section>
    </>}
  </main>;
}
