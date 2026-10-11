import { Fragment, useCallback, useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { AudioLines, BookOpen, Check, Plus, Trash2 } from 'lucide-react';
import { HostBridge } from './bridge';
import { ContextMenu, type ContextMenuState } from './pi/ContextMenu';
import { Modal } from './ui';
import './speech-dictionaries.css';

export type Dictionary = {path: string; name: string; exists: boolean; active: boolean};
export type DictionaryResult = {dictionaries: Dictionary[]; theme: string};
export type ResourceResult = {dictionaries?: Dictionary[]; models?: Dictionary[]; theme: string};
export type DictionaryRpc = (method: string, params: Record<string, unknown>) => Promise<ResourceResult>;

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
  // The category navigation uses the AI chat settings page's markup and styles.
  return <section className="settings-dashboard dictionary-manager">
    <nav className="settings-nav dictionary-nav" aria-label="词典与模型分类"><h2>对齐资源</h2>
      {categories.map(item => <Fragment key={item.id}>
        {item.directory && <hr className="dictionary-nav-divider"/>}
        <button aria-current={category.id === item.id ? 'page' : undefined} className={category.id === item.id ? 'selected' : ''} onClick={() => setCategory(item)}><item.Icon size={18}/>{'navLabel' in item ? item.navLabel : item.label}</button>
      </Fragment>)}
    </nav>
    <ResourceList key={category.id} category={category} rpc={rpc}/>
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
  const closeMenu = useCallback(() => setMenu(null), []);
  const run = async (method: string, params = {}) => {
    setBusy(true); setError('');
    try { setData(await rpc(method, params)); return true; }
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
      <div className="actions"><button disabled={busy} onClick={() => setRemoving(undefined)}>取消</button><button className="danger" disabled={busy} onClick={() => {void run(`${id}.remove`, {path: removing.path}).then(ok => {if (ok) setRemoving(undefined);});}}>删除</button></div>
    </Modal>}
  </main>;
}
