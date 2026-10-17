import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import type { ThreadComposerRuntime } from '@assistant-ui/react';
import { Bot, Check, ChevronDown, Search, Settings2, X } from 'lucide-react';
import { ChatStore, useChatState } from './store';
import { knownModel, type Bootstrap, type Config, type ContextStatus, type Session } from './types';

type Scope = 'api' | 'local';
const strengths = [['auto', '自动'], ['off', '关闭'], ['low', '低'], ['medium', '中'], ['high', '高']] as const;
export function thinkingStrength(config: Config, scope: Scope) {
  return scope === 'api' && config.force_deep_thinking ? 'high' : String(config.thinking_level || 'auto');
}
export function quickModelPatch(scope: Scope, model: string) {
  return scope === 'api' ? {api: {enabled: true, model}} : {api: {enabled: false}, local: {model}};
}
export function quickStrengthPatch(scope: Scope, level: string) {
  return scope === 'api' ? {api: {thinking_level: level, force_deep_thinking: false}} : {local: {thinking_level: level, ...(level === 'auto' ? {} : {enable_thinking: level !== 'off'})}};
}
export function modelOptions(boot: Bootstrap, scope: Scope) {
  const config = boot.settings[scope];
  // A same-name model at a gateway is not an official capability match.
  let official = false;
  try { official = new URL(String(config.base_url || '')).hostname.toLowerCase() === 'api.openai.com'; } catch { /* unconfigured */ }
  return [...new Set([String(config.model || ''), ...(official ? boot.models.map(m => m.id) : [])])].filter(Boolean);
}
const number = (value: number | null | undefined) => value == null ? '未知' : value.toLocaleString();

function usePopoverPosition(trigger: RefObject<HTMLButtonElement | null>, popup: RefObject<HTMLDivElement | null>, open: boolean) {
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      if (!trigger.current || !popup.current) return;
      const rect = trigger.current.getBoundingClientRect();
      Object.assign(popup.current.style, {
        right: `${Math.min(Math.max(12, window.innerWidth - rect.right), Math.max(12, window.innerWidth - popup.current.offsetWidth - 12))}px`,
        bottom: `${window.innerHeight - rect.top + 8}px`,
        maxHeight: `${Math.max(100, rect.top - 20)}px`,
      });
    };
    position(); window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
    return () => { window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true); };
  }, [open, trigger, popup]);
}

export function ComposerStatus({store, session, composer, onSettings}: {store: ChatStore; session: Session; composer: ThreadComposerRuntime; onSettings: () => void}) {
  const state = useChatState(store); const boot = state.boot!;
  const activeScope: Scope = boot.settings.api.enabled ? 'api' : 'local';
  const config = boot.settings[activeScope]; const strength = thinkingStrength(config, activeScope);
  const [scope, setScope] = useState<Scope>(activeScope); const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false); const [saveError, setSaveError] = useState('');
  const [context, setContext] = useState<ContextStatus>(); const [contextError, setContextError] = useState('');
  const [inputs, setInputs] = useState({text: composer.getState().text, attachmentIds: [] as string[]});
  const [contextOpen, setContextOpen] = useState(false); const [modelOpen, setModelOpen] = useState(false);
  const contextId = useId(); const modelId = useId();
  const contextPopup = useRef<HTMLDivElement>(null); const modelPopup = useRef<HTMLDivElement>(null); const search = useRef<HTMLInputElement>(null);
  const contextTrigger = useRef<HTMLButtonElement>(null); const modelTrigger = useRef<HTMLButtonElement>(null);
  usePopoverPosition(contextTrigger, contextPopup, contextOpen); usePopoverPosition(modelTrigger, modelPopup, modelOpen);
  const messages = state.messages[session.id] || [];
  // Running text is intentionally excluded by the host's next-turn context policy.
  const historyRevision = messages.map(m => `${m.id}:${m.status}:${m.status === 'running' ? 0 : m.content.length}`).join('|');
  useEffect(() => {
    const sync = () => {
      const current = composer.getState();
      const attachmentIds = current.attachments.filter(a => a.status.type !== 'running' && a.status.type !== 'incomplete').map(a => a.id);
      setInputs(old => old.text === current.text && old.attachmentIds.join('|') === attachmentIds.join('|') ? old : {text: current.text, attachmentIds});
    };
    sync(); return composer.subscribe(sync);
  }, [composer]);
  useEffect(() => {
    // A different conversation/configuration cannot reuse this estimate.
    setContext(undefined); setContextError('');
  }, [session.id, boot.settings]);
  useEffect(() => {
    let cancelled = false;
    // Keep the last valid value while the same conversation's draft changes.
    // Clearing here blanks the ring on every key, before debounce even runs.
    setContextError('');
    const timer = setTimeout(() => {
      void store.rpc('sessions.context', {sessionId: session.id, ...inputs}).then(value => {
        if (!cancelled && value.sessionId === session.id) setContext(value);
      }).catch(error => { if (!cancelled) { setContext(undefined); setContextError(error instanceof Error ? error.message : String(error)); } });
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [store, session.id, inputs, boot.settings, historyRevision]);
  useEffect(() => { setScope(activeScope); }, [activeScope]);
  const percent = context?.percent;
  const percentLabel = percent == null ? context || contextError ? '—' : '…' : `${Math.round(percent)}%`;
  const disabled = saving || Boolean(state.submitting[session.id]) || !state.connected;
  const options = modelOptions(boot, scope).filter(model => model.toLowerCase().includes(query.trim().toLowerCase()));
  const canChoose = Boolean(boot.settings[scope].base_url) && !(scope === 'local' && boot.settings.api.locked);
  async function save(patch: Parameters<ChatStore['save']>[0]) {
    if (disabled) return;
    setSaving(true); setSaveError('');
    try { await store.save(patch); } catch (error) { setSaveError(error instanceof Error ? error.message : String(error)); }
    finally { setSaving(false); }
  }
  function openSettings() { contextPopup.current?.hidePopover(); modelPopup.current?.hidePopover(); onSettings(); }
  return <div className="composer-status">
    <button ref={contextTrigger} type="button" className={`composer-context-trigger ${percent != null && percent >= 85 ? 'near-limit' : ''}`} popoverTarget={contextId} aria-label="上下文用量" aria-expanded={contextOpen} title="上下文用量（估算）">
      <svg className="context-ring" width="18" height="18" viewBox="0 0 20 20" aria-hidden="true"><circle className="ring-track" cx="10" cy="10" r="8"/><circle className="ring-value" cx="10" cy="10" r="8" pathLength="100" strokeDasharray={`${percent == null ? 0 : Math.min(100, Math.max(0, percent))} 100`} transform="rotate(-90 10 10)"/></svg><span>{percentLabel}</span>
    </button>
    <button ref={modelTrigger} type="button" className="composer-model-trigger" popoverTarget={modelId} aria-label="切换模型与推理强度" aria-expanded={modelOpen} title="切换模型与推理强度"><Bot size={16}/><span>{String(config.model || '未配置模型')}<span className="model-strength"> · {strength}</span></span><ChevronDown size={13}/></button>
    <div ref={contextPopup} id={contextId} popover="auto" className="composer-popover context-popover" role="dialog" aria-label="上下文用量详情" onToggle={e => setContextOpen((e.nativeEvent as ToggleEvent).newState === 'open')}>
      <header><strong>上下文用量</strong><span className="popover-tag">估算</span><button type="button" className="popover-close" aria-label="关闭上下文详情" onClick={() => contextPopup.current?.hidePopover()}><X size={15}/></button></header>
      <div className="context-summary"><strong>{percentLabel}</strong><span>{number(context?.inputTokens)} / {number(context?.contextWindow)} tokens</span></div>
      <div className="context-meter"><span style={{width: `${percent == null ? 0 : Math.min(100, Math.max(0, percent))}%`}}/></div>
      {context ? <><dl className="context-values"><div><dt>输入上下文</dt><dd>{number(context.inputTokens)}</dd></div><div><dt>其中指令与工具定义</dt><dd>{number(context.overheadTokens)}</dd></div><div><dt>上下文窗口</dt><dd>{number(context.contextWindow)}</dd></div><div><dt>{context.tokenMode === 'provider' ? '压缩预留（非输出上限）' : '输出预留'}</dt><dd>{number(context.reservedTokens)}</dd></div><div><dt>可用余量</dt><dd className={(context.availableTokens ?? 0) < 0 ? 'budget-overflow' : ''}>{number(context.availableTokens)}</dd></div></dl><p className="popover-footnote">{({auto:'自动预算',manual:'手动预算',provider:'服务商决定'} as Record<string,string>)[context.tokenMode]} · {context.windowSource === 'metadata' ? '官方模型元数据' : context.windowSource === 'configured' ? '当前配置窗口' : '窗口未知'}</p>{context.reason && <p className="popover-error">{context.reason}</p>}<p className="popover-footnote">按当前摘要、已完成历史、专业证据、草稿与文本附件估算，非服务商精确用量。不含{context.exclusions.join('、')}。</p></> : <p className={contextError ? 'popover-error' : 'popover-footnote'} role={contextError ? 'alert' : 'status'}>{contextError || '正在读取宿主预算…'}</p>}
      <button type="button" className="popover-settings" onClick={openSettings}><Settings2 size={14}/>上下文与模型设置</button>
    </div>
    <div ref={modelPopup} id={modelId} popover="auto" className="composer-popover model-popover" role="dialog" aria-label="模型与推理强度" onToggle={e => {
      const open = (e.nativeEvent as ToggleEvent).newState === 'open'; setModelOpen(open);
      if (open) { setQuery(''); setSaveError(''); setScope(activeScope); search.current?.focus(); }
    }}>
      <header><strong>切换模型</strong><button type="button" className="popover-close" aria-label="关闭模型选择" onClick={() => modelPopup.current?.hidePopover()}><X size={15}/></button></header>
      <label className="model-search"><Search size={15}/><input ref={search} aria-label="搜索或输入模型名" placeholder="搜索或输入模型名…" value={query} onChange={e => setQuery(e.target.value)}/></label>
      <div className="model-scopes" role="group" aria-label="模型配置范围"><button type="button" aria-pressed={scope === 'api'} onClick={() => setScope('api')}>API</button><button type="button" aria-pressed={scope === 'local'} onClick={() => setScope('local')}>本地</button></div>
      <div className="quick-model-list" role="listbox" aria-label="可选模型">{options.map(model => <button type="button" role="option" key={model} aria-selected={scope === activeScope && model === config.model} disabled={disabled || !canChoose} onClick={() => void save(quickModelPatch(scope, model))}><Bot size={16}/><span>{model}<small>{scope === 'local' ? '已配置本地端点' : String(boot.settings.api.label || '已配置 API 端点')}{knownModel(boot.models, {...boot.settings[scope], model}) ? ' · 已适配窗口' : ''}</small></span>{scope === activeScope && model === config.model && <Check size={15}/>}</button>)}{query.trim() && !options.includes(query.trim()) && <button type="button" role="option" aria-selected={false} disabled={disabled || !canChoose} onClick={() => void save(quickModelPatch(scope, query.trim()))}><Bot size={16}/><span>{query.trim()}<small>使用自定义模型名 · 不更换端点</small></span></button>}{!options.length && !query.trim() && <p className="popover-footnote">尚未配置模型，可输入模型名或打开完整设置。</p>}</div>
      {!canChoose && <p className="popover-error">{scope === 'local' && boot.settings.api.locked ? 'API 已锁定，请先在设置中解除锁定。' : '请先在设置中配置此范围的 API 地址。'}</p>}
      <section className="quick-thinking"><h3>推理强度 <small>当前模型 · {activeScope === 'api' ? 'API' : '本地'}</small></h3><div role="group" aria-label="推理强度">{strengths.map(([value, label]) => <button type="button" key={value} aria-pressed={strength === value} disabled={disabled} title={value} onClick={() => void save(quickStrengthPatch(activeScope, value))}>{label}</button>)}</div><p className="popover-footnote">{activeScope === 'local' ? '本地 low / medium / high 均表示开启思考；没有细分强度。' : '具体支持由当前模型决定；不会在切换时发出验证请求。'}</p></section>
      {saveError && <p className="popover-error" role="alert">{saveError}</p>}<footer><span role="status">{saving ? '保存中…' : '仅用于后续任务 · 运行中任务不变'}</span><button type="button" className="popover-settings" onClick={openSettings}><Settings2 size={14}/>设置</button></footer>
    </div>
  </div>;
}
