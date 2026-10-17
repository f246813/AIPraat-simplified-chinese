import { Fragment, useCallback, useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { AudioLines, BookOpen, Check, Plus, Trash2 } from 'lucide-react';
import { HostBridge } from './bridge';
import { ContextMenu, type ContextMenuState } from './pi/ContextMenu';
import { Modal } from './ui';
import { MfaPresetEditor, MfaStatusView, type MfaData, type Resource } from './MfaPresetEditor';
import './speech-dictionaries.css';

export type Dictionary = {path: string; name: string; exists: boolean; active: boolean};
export type DictionaryResult = {dictionaries: Dictionary[]; theme: string};
export type ResourceResult = {dictionaries?: Dictionary[]; models?: Dictionary[]; theme: string};
export type DictionaryRpc = (method: string, params: Record<string, unknown>) => Promise<unknown>;

export const dictionaryRpc: DictionaryRpc = async (method, params) => {
  await new HostBridge().wait();
  return await window.pywebview!.api!.rpc!(method, params) as ResourceResult;
};

const categories = [
  {id: 'dictionaries', label: 'MFA语音词典', noun: '词典', result: 'dictionaries', Icon: BookOpen, directory: false},
  {id: 'acoustic_models', label: 'MFA声学模型', noun: '模型', result: 'models', Icon: AudioLines, directory: false},
  {id: 'wav2vec2_models', label: 'Wav2Vec2声学模型', navLabel: 'Wav2Vec2模型', noun: '模型', result: 'models', Icon: AudioLines, directory: true},
] as const;
type Category = typeof categories[number];

export function SpeechDictionaries({rpc = dictionaryRpc}: {rpc?: DictionaryRpc}) {
  const [category, setCategory] = useState<Category>(categories[0]);
  const [mfaPage, setMfaPage] = useState<'' | 'g2p' | 'presets'>('');
  // The category navigation uses the AI chat settings page's markup and styles.
  return <section className="settings-dashboard dictionary-manager">
    <nav className="settings-nav dictionary-nav" aria-label="词典与模型分类"><h2>对齐资源</h2>
      {categories.map(item => <Fragment key={item.id}>
        {item.directory && <hr className="dictionary-nav-divider"/>}
        <button aria-current={!mfaPage && category.id === item.id ? 'page' : undefined} className={!mfaPage && category.id === item.id ? 'selected' : ''} onClick={() => {setMfaPage(''); setCategory(item);}}><item.Icon size={18}/>{'navLabel' in item ? item.navLabel : item.label}</button>
      </Fragment>)}
      <div aria-hidden="true" className="dictionary-nav-divider"/>
      <button aria-current={mfaPage === 'g2p' ? 'page' : undefined} className={mfaPage === 'g2p' ? 'selected' : ''} onClick={() => setMfaPage('g2p')}><AudioLines size={18}/>MFA G2P 模型</button>
      <button aria-current={mfaPage === 'presets' ? 'page' : undefined} className={mfaPage === 'presets' ? 'selected' : ''} onClick={() => setMfaPage('presets')}><BookOpen size={18}/>纠音资源配套</button>
    </nav>
    {mfaPage ? <MfaResources key={mfaPage} page={mfaPage} rpc={rpc}/> : <ResourceList key={category.id} category={category} rpc={rpc}/>}
  </section>;
}

function ResourceList({category, rpc}: {category: Category; rpc: DictionaryRpc}) {
  const {id, label, noun, Icon} = category;
  const [data, setData] = useState<ResourceResult>();
  const items = data?.[category.result] || [];
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [removing, setRemoving] = useState<Dictionary>();
  const [affected, setAffected] = useState(''); const [checkingReferences, setCheckingReferences] = useState(false);
  useEffect(() => {
    if (!removing || category.directory) return;
    let alive = true; setCheckingReferences(true); setAffected('正在查询引用预设…');
    void rpc('mfa.list', {}).then(result => {
      if (!alive) return; const mfa = result as MfaData;
      if (!mfa.resources || !mfa.presets) {setAffected('未能读取引用预设；移除可能导致纠音配套缺资源。'); return;}
      const kind = id === 'dictionaries' ? 'dictionary' : 'acoustic';
      const normalize = (path: string) => path.replace(/\\/g, '/').toLowerCase();
      const resource = mfa.resources[kind].find(r => normalize(r.path) === normalize(removing.path));
      setAffected(resource?.used_by.map(presetId => mfa.presets.find(p => p.id === presetId)?.name || presetId).join('、') || '无');
    }).catch(() => {if (alive) setAffected('无法读取引用预设；请先检查纠音配套。');}).finally(() => {if (alive) setCheckingReferences(false);});
    return () => {alive = false;};
  }, [removing, id, category.directory, rpc]);
  const closeMenu = useCallback(() => setMenu(null), []);
  const run = async (method: string, params = {}) => {
    setBusy(true); setError('');
    try { setData(await rpc(method, params) as ResourceResult); return true; }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(false); }
  };
  useEffect(() => { void run(`${id}.list`); }, [rpc, id]);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const update = () => { document.documentElement.dataset.theme = data?.theme === 'dark' || data?.theme !== 'light' && media.matches ? 'dark' : 'light'; };
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [data?.theme]);
  const openMenu = (item: Dictionary, event: MouseEvent<HTMLButtonElement> | KeyboardEvent<HTMLButtonElement>) => {
    event.preventDefault();
    if (busy) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setMenu({label: `${item.name} 的操作`, point: 'clientX' in event ? {x: event.clientX, y: event.clientY} : {x: rect.left + 20, y: rect.bottom},
      trigger: event.currentTarget, items: [
        {id: 'select', label: `选用该${noun}`, disabled: item.active || !item.exists, icon: <Check size={15}/>, onSelect: () => {void run(`${id}.select`, {path: item.path});}},
        {id: 'remove', label: '删除', danger: true, separatorBefore: true, icon: <Trash2 size={15}/>, onSelect: () => setRemoving(item)}
      ]});
  };
  return <main className="dictionary-window">
    <header><div className="dictionary-mark"><Icon size={23}/></div><div><h1>管理{label}</h1><p className="muted">已配置的{noun} · 右键条目可选用或删除</p></div></header>
    {error && <div className="dictionary-error" role="alert">{error}<button disabled={busy} onClick={() => void run(`${id}.list`)}>重新加载</button></div>}
    <section className="dictionary-list" aria-label={`已配置的${label}`} aria-busy={busy}>
      {!data ? <p className="dictionary-empty">{busy ? `正在加载${noun}…` : `${noun}列表加载失败`}</p> : items.length ? items.map(item =>
        <button key={item.path} className={`dictionary-row${item.active ? ' is-active' : ''}`} aria-current={item.active ? 'true' : undefined} disabled={busy} onContextMenu={event => openMenu(item, event)} onKeyDown={event => {if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') openMenu(item, event);}} title={item.path}>
          <Icon size={19}/><span><strong>{item.name}</strong><small>{item.path}</small></span>
          {item.active && <span className="dictionary-current"><Check size={18} strokeWidth={2.5} aria-hidden="true"/>当前</span>}{!item.exists && <span className="badge bad">{category.directory ? '目录不存在' : '文件不存在'}</span>}
        </button>) : <div className="dictionary-empty"><Icon size={30}/><p>尚未配置{label}</p><small>点击下方按钮选择{noun}{category.directory ? '目录' : '文件'}</small></div>}
    </section>
    <footer><button className="primary" disabled={busy} onClick={() => void run(`${id}.choose`)}><Plus size={17}/>{busy && data ? '处理中…' : `添加${label}`}</button><small>{items.length} 个{noun}</small></footer>
    <ContextMenu state={menu} onClose={closeMenu}/>
    {removing && <Modal title={`删除${label}`} onClose={() => {if (!busy) setRemoving(undefined);}}>
      <p>从配置中删除“{removing.name}”？{noun}{category.directory ? '目录' : '文件'}会保留在磁盘上。</p>
      {removing.active && <p className="muted">删除后，当前{noun}将切换为列表中的下一个{noun}；列表为空时清除当前路径。</p>}
      {!category.directory && <p className="muted">受影响纠音预设：{affected || '正在查询…'}。配套会标记为缺资源，不会自动替换绑定；上述活动路径切换仅适用于旧全局对齐配置。</p>}
      <div className="actions"><button disabled={busy} onClick={() => setRemoving(undefined)}>取消</button><button className="danger" disabled={busy || checkingReferences} onClick={() => {void run(`${id}.remove`, {path: removing.path}).then(ok => {if (ok) setRemoving(undefined);});}}>删除</button></div>
    </Modal>}
  </main>;
}

function MfaResources({page, rpc}: {page: 'g2p' | 'presets'; rpc: DictionaryRpc}) {
  const [data, setData] = useState<MfaData>(); const [busy, setBusy] = useState(true); const [error, setError] = useState('');
  const [editorId, setEditorId] = useState(''); const [removing, setRemoving] = useState<Resource>();
  const run = async (method: string, params: Record<string, unknown> = {}) => {
    setBusy(true); setError(''); try {setData(await rpc(method, params) as MfaData); return true;}
    catch(e) {setError(e instanceof Error ? e.message : String(e)); return false;} finally {setBusy(false);}
  };
  useEffect(() => {let alive = true; void rpc('mfa.list', {}).then(result => {if (alive) {setData(result as MfaData); setBusy(false);}}).catch(e => {if (alive) {setError(e instanceof Error ? e.message : String(e)); setBusy(false);}}); return () => {alive = false;};}, [rpc]);
  useEffect(() => {const media = matchMedia('(prefers-color-scheme: dark)'); const update = () => {document.documentElement.dataset.theme = data?.theme === 'dark' || data?.theme !== 'light' && media.matches ? 'dark' : 'light';}; update(); media.addEventListener('change', update); return () => media.removeEventListener('change', update);}, [data?.theme]);
  const usedNames = (resource: Resource) => resource.used_by.map(id => data?.presets.find(p => p.id === id)?.name || id).join('、') || '无';
  return <main className="dictionary-window"><header><div className="dictionary-mark"><AudioLines size={23}/></div><div><h1>{page === 'g2p' ? '管理 MFA G2P 模型' : '纠音资源配套'}</h1><p className="muted">G2P 生成读音；声学模型对齐音频。配套绑定独立于旧全局活动资源。</p></div></header>
    {error && <div className="dictionary-error" role="alert">{error}<button disabled={busy} onClick={() => void run('mfa.list')}>重新加载</button></div>}
    <section className="dictionary-list" aria-label={page === 'g2p' ? '已登记的 MFA G2P 模型' : '语言读音配套'} aria-busy={busy}>
      {!data ? <p className="dictionary-empty">{busy ? '正在加载…' : '资源加载失败'}</p> : page === 'g2p' ? data.resources.g2p.length ? data.resources.g2p.map(r => <div key={r.id} className="dictionary-row mfa-library-row"><AudioLines size={19}/><span><strong>{r.name}</strong><small>{r.path}</small><small>引用预设：{usedNames(r)}</small></span>{!r.exists && <span className="badge bad">文件不存在</span>}<button disabled={busy} onClick={() => setRemoving(r)} aria-label={`移除 ${r.name} 登记`}><Trash2 size={16}/>移除登记</button></div>) : <div className="dictionary-empty"><p>尚未登记 MFA G2P 模型</p><small>添加本地模型后，在纠音资源配套中选择绑定。</small></div> : data.presets.map(p => <article className="mfa-preset-card" key={p.id}><h2>{p.name}{data.default_preset === p.id && <span className="badge">默认</span>}</h2><MfaStatusView status={p.status}/><button disabled={busy} onClick={() => setEditorId(p.id)}>配置 {p.name} 配套资源</button></article>)}
    </section>
    <footer>{page === 'g2p' && <button className="primary" disabled={busy} onClick={() => void run('mfa.choose', {kind: 'g2p'})}><Plus size={17}/>添加 MFA G2P 模型</button>}<small>移除登记不删除磁盘文件，也不会自动替换引用预设。</small></footer>
    {data && editorId && <MfaPresetEditor key={editorId} data={data} presetId={editorId} rpc={rpc} onData={setData} onClose={() => setEditorId('')}/>}
    {removing && <Modal title="移除 G2P 模型登记" onClose={() => {if (!busy) setRemoving(undefined);}}><p>移除“{removing.name}”的登记？文件会保留在磁盘上。</p><p>受影响预设：{usedNames(removing)}。引用将标记为缺资源，不会自动换为其他口音模型。</p><div className="actions"><button disabled={busy} onClick={() => setRemoving(undefined)}>取消</button><button className="danger" disabled={busy} onClick={() => void run('mfa.remove', {kind: 'g2p', id: removing.id}).then(ok => {if (ok) setRemoving(undefined);})}>移除登记</button></div></Modal>}
  </main>;
}
