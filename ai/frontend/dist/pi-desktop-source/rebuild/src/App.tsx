import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { AudioLines, CircleAlert, X } from 'lucide-react';
import { ChatStore, shallowEqual, useChatSelector } from './store';
import { HostBridge } from './bridge';
import { Chat } from './Chat';
import { SettingsDashboard, type SettingsCategory } from './Settings';
import { activeTask, type Session } from './types';
import { IconButton, Modal, statusLabel } from './ui';
import { SessionSidebar } from './pi/sidebar/Sidebar';
import { useSidebarBudget, useStoredWidth } from './Panes';
import { clampSidebarWidth } from './layout';
export function App({bridge, store, demo = false}: {bridge: HostBridge; store: ChatStore; demo?: boolean}) {
  // The shell consumes boot, navigation, tasks, the session list, selection and error;
  // a growing reply changes none of these, so the shell and sidebar skip the commit.
  const state = useChatSelector(store, s => ({boot: s.boot, modelSettingsRequest: s.modelSettingsRequest, reportRequest: s.reportRequest, tasks: s.tasks, sessions: s.sessions, selected: s.selected, error: s.error}), shallowEqual); const [sidebar, setSidebar] = useState(false); const [settings, setSettings] = useState<SettingsCategory|null>(null);
  const [starting, setStarting] = useState(true);
  const [modelSettingsRequest, setModelSettingsRequest] = useState<string>();
  const [edit, setEdit] = useState<{session: Session}>(); const [title, setTitle] = useState(''); const [busy, setBusy] = useState(false); const [taskPanel, setTaskPanel] = useState(false);
  // The sidebar width and its live budget are separate from the collapsed/expanded rail state.
  const app = useRef<HTMLDivElement>(null); const sidebarWidth = useStoredWidth(store, 'sidebar_width', clampSidebarWidth); const sidebarBudget = useSidebarBudget(app);
  const initialize = async () => { setStarting(true); store.clearError(); try { await bridge.wait(); await store.initialize({newSession: !store.getSnapshot().boot}); store.start(); } catch (e) { store.report(e); } finally { setStarting(false); } };
  useEffect(() => { void initialize(); const flush = () => { for (const s of store.getSnapshot().sessions) store.flushView(s.id); }; window.addEventListener('pagehide', flush); return () => { window.removeEventListener('pagehide', flush); store.stop(); }; }, [store, bridge]);
  useEffect(() => { if (state.modelSettingsRequest) { setSettings('模型'); setModelSettingsRequest(state.modelSettingsRequest); } }, [state.modelSettingsRequest]);
  useEffect(() => { if (state.reportRequest) setSettings(null); }, [state.reportRequest]);
  useEffect(() => {
    const prefs = state.boot?.settings.preferences;
    const media = matchMedia('(prefers-color-scheme: dark)');
    const setTheme = () => { document.documentElement.dataset.theme = prefs?.theme === 'dark' || (!prefs?.theme || prefs.theme === 'system') && media.matches ? 'dark' : 'light'; document.documentElement.style.setProperty('--chat-font-size', `${Number(prefs?.font_size) || 15}px`); };
    setTheme(); media.addEventListener('change', setTheme); return () => media.removeEventListener('change', setTheme);
  }, [state.boot?.settings.preferences]);
  const tasks = Object.values(state.tasks).filter(activeTask);
  const selected = state.sessions.find(s => s.id === state.selected);
  if (!state.boot) return <main className="host-screen"><div className="welcome-mark"><AudioLines size={32}/></div><h1>Praat 声学助手</h1><p>{starting ? '正在等待桌面宿主…' : '需要桌面宿主连接'}</p>{state.error && <div role="alert" className="warning">{state.error}</div>}<p className="muted">请从 AIPraat 打开 AI 工作台</p><button className="primary" disabled={starting} onClick={() => void initialize()}>重新连接宿主</button></main>;
  return <div ref={app} className={`app ${sidebar ? 'sidebar-expanded' : ''}`} style={{'--sidebar-width': `${sidebarWidth.width}px`} as CSSProperties}>
    {demo && <div className="demo-banner" role="status">浏览器测试适配器 · 全部内容为明确的演示夹具 · 没有模型、Praat 或网络验证</div>}
    {!settings && <SessionSidebar store={store} expanded={sidebar} onToggle={() => setSidebar(s => !s)} width={sidebarWidth.width} maxWidth={sidebarBudget} onPreviewWidth={sidebarWidth.preview} onCommitWidth={sidebarWidth.commit} onTasks={() => setTaskPanel(true)} onSettings={() => setSettings(sidebar ? '会话与存储' : '模型')} onRename={session => { setEdit({session}); setTitle(session.title); }} onDelete={session => { void store.delete(session.id).catch(store.report); }}/>}
    <main className="main-surface">{state.error && <div className="error-banner" role="alert"><CircleAlert size={16}/><span>{state.error}</span><button onClick={() => void store.initialize().catch(store.report)}>刷新核对</button><IconButton label="关闭错误提示" onClick={store.clearError}><X size={16}/></IconButton></div>}{settings ? <SettingsDashboard store={store} initialCategory={settings} modelSettingsRequest={modelSettingsRequest} onClose={() => { setSettings(null); setModelSettingsRequest(undefined); }}/> : selected ? <Chat key={selected.id} store={store} session={selected} onSettings={() => setSettings('模型')}/> : <div className="host-screen"><AudioLines size={38}/><h1>准备开始新的分析</h1><p className="muted">创建会话，开始分析录音</p><button className="primary" onClick={() => void store.create().catch(store.report)}>新建会话</button></div>}</main>
    {edit && <Modal title="重命名会话" onClose={() => !busy && setEdit(undefined)}><label className="field"><span>会话名称</span><input autoFocus value={title} onChange={e => setTitle(e.target.value)} maxLength={200}/></label><div className="actions"><button disabled={busy} onClick={() => setEdit(undefined)}>取消</button><button className="primary" disabled={busy || !title.trim()} onClick={() => { setBusy(true); void store.rename(edit.session.id,title).then(() => setEdit(undefined)).catch(store.report).finally(() => setBusy(false)); }}>{busy ? '处理中…' : '确认'}</button></div></Modal>}
    {taskPanel && <Modal title="后台任务" onClose={() => setTaskPanel(false)}><p className="muted">任务在切换会话后继续运行</p>{tasks.length ? tasks.map(t => <div className="task-row" key={t.id}><div><strong>{state.sessions.find(s => s.id === t.sessionId)?.title || t.sessionId}</strong><small>{statusLabel(t.status)} · {t.model}<br/>{t.id}</small></div><button onClick={() => { setTaskPanel(false); void store.select(t.sessionId).catch(store.report); }}>查看</button><button onClick={() => void store.rpc('tasks.cancel',{taskId:t.id}).catch(store.report)}>取消任务</button></div>) : <p>没有运行中的任务</p>}</Modal>}
  </div>;
}
