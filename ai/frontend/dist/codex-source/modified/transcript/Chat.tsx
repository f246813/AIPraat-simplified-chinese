import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useAuiState, ThreadPrimitive, MessagePrimitive, type AssistantRuntime, type AttachmentAdapter, type ThreadMessageLike } from '@assistant-ui/react';
import { ArrowDown, Brain, CircleAlert, Copy, FileText, Folder, LoaderCircle, RefreshCw, Search, Shield, Wrench } from 'lucide-react';
import praatIcon from '../../../icon.png';
import { Markdown } from './Markdown';
import { Composer, AttachmentPreview, attachmentDescriptor } from './Composer';
import { importFile } from './bridge';
import { draftFollowup, followupContinuation, type FollowupDraft } from './composer-send';
import { ChatStore, useChatState } from './store';
import { knownModel, readOnly, type Activity, type Message, type Preview, type Session } from './types';
import { IconButton, Json, statusLabel } from './ui';
import { ConversationWidthHandles, useStoredWidth } from './Panes';
import { useDisclosureAnchor } from './Anchoring';
import { ContextMenu } from './pi/ContextMenu';
import { messageCopyPayload, returnToLatest, selectContents, transcriptMenuItems, type TranscriptMenuState } from './TranscriptMenu';
import { collectEvidence, conversationMenuEntries, evidencePayload, messageMenuEntries, transcriptText, type EvidenceEntry, type TranscriptMenuAction } from './transcript-menu';
import { resolveConversationWidth } from './layout';
import { anchorFor, growTranscriptWindow, HISTORY_REVEAL_THRESHOLD_PX, prependedHeight, reduceTranscriptWindow, scrollTopFor, TRANSCRIPT_WINDOW_MIN, type RowOffset } from './transcript-window';
import { ConversationMinimap } from './MinimapRail';
import { conversationMarkers } from './minimap';
import { ChatSearch } from './SearchBar';
import { activityState } from './codex/activity-state';
/** A menu row publishes only the data it owns; the transcript owns the single floating layer. */
type TranscriptMenuRequest = {kind: 'message'; text: string; evidence: EvidenceEntry[]} | {kind: 'conversation'};
const Context = createContext<{store: ChatStore; sessionId:string; smooth: boolean; disclosure: (element: HTMLElement) => void; menu: (event: ReactMouseEvent<HTMLElement>, request: TranscriptMenuRequest) => void; draft: (mode: 'L2' | 'L3' | 'L4', taskId: string) => void}>(null!);
export function convertMessage(message: Message): ThreadMessageLike {
  const status = message.status === 'running' ? {type: 'running' as const} : message.status === 'failed' ? {type: 'incomplete' as const, reason: 'error' as const} : ['partial','cancelled','interrupted'].includes(message.status || '') ? {type: 'incomplete' as const, reason: 'cancelled' as const} : {type: 'complete' as const, reason: 'stop' as const};
  return {id: message.id, role: message.role, content: message.content, ...(message.role === 'assistant' ? {status} : {}), metadata: {custom: {host: message}}, ...(message.role === 'user' ? {attachments: message.attachments?.map(a => ({...attachmentDescriptor(a), status: {type: 'complete' as const}}))} : {})};
}
function ActivityCard({activity}: {activity: Activity}) {
  const {store, disclosure} = useContext(Context);
  const tool = activity.type === 'tool'; const thinking = activity.type === 'thinking';
  const Icon = tool ? Wrench : thinking ? Brain : activity.type === 'compression' ? RefreshCw : LoaderCircle;
  if (thinking && !activity.text) return null;
  // Anchor before `<details>` flips, so expanding a card above the viewport does not move the reading position.
  return <details className={`activity ${tool ? 'tool-card' : ''}`} data-activity-id={activity.id}><summary onClick={event => disclosure(event.currentTarget)}><Icon size={15}/><span>{tool ? activity.name || '工具执行' : thinking ? '模型思考' : activity.type === 'compression' ? '上下文压缩' : '执行进度'}</span><span className={`badge ${activity.status === 'failed' ? 'bad' : ''}`}>{statusLabel(activity.status)}</span></summary><div className="activity-body">{activity.execution && <p className="execution-fact"><Shield size={14}/>投递 / 执行事实：<strong>{activity.execution}</strong></p>}{activity.text && <Markdown text={activity.text} rpc={store.rpc} onError={store.report}/>} {activity.args !== undefined && <><h5>参数</h5><Json value={activity.args}/></>}{activity.result !== undefined && <><h5>结果 / 结构化证据</h5><Json value={activity.result}/></>}{tool && !activity.execution && <p className="muted">宿主未提供投递事实。成功或取消状态不等于已执行或撤销。</p>}</div></details>;
}
/** One disclosure per reply; streaming updates never force it open. */
function ProcessDetails({message}: {message: Message}) {
  const {disclosure} = useContext(Context);
  const activities = (message.activities || []).filter(a => a.type !== 'thinking' || a.text);
  const results = message.results || [];
  if (!activities.length && !results.length) return null;
  const running = message.status === 'running';
  const failed = activities.some(a => a.status === 'failed' || a.status === 'error') || results.some(r => r.result_valid === false || r.status === 'failed' || r.status === 'error');
  const state = activityState(failed, running);
  const status = running ? '执行中' : message.status && message.status !== 'complete' ? statusLabel(message.status) : '执行过程';
  return <details className="process-details" data-process-state={state}>
    <summary onClick={event => disclosure(event.currentTarget)}>
      {running && <LoaderCircle size={13} aria-hidden="true"/>}
      <span>{status}</span>
      <span className="process-count">{activities.length ? `${activities.length} 个步骤` : `${results.length} 项工具记录`}</span>
      {failed && <span className="process-failure">含失败步骤</span>}
    </summary>
    <div className="process-body">
      {activities.length > 0 && <div className="activities">{activities.map(a => <ActivityCard key={a.id} activity={a}/>)}</div>}
      {results.length > 0 && <section className="process-results" aria-label="工具执行记录">{results.map(fact => <details key={fact.id}><summary onClick={event => disclosure(event.currentTarget)}>{fact.tool} · {fact.result_valid === false ? '结果不可用' : statusLabel(fact.status)} · {fact.execution || '宿主未报告投递状态'}</summary><Json value={fact}/></details>)}</section>}
    </div>
  </details>;
}
function MessageCard() {
  const {store, sessionId, smooth, menu, draft} = useContext(Context); const m = useAuiState(s => s.message);
  const host = m.metadata.custom.host as Message | undefined;
  const text = host?.content ?? m.content.filter(p => p.type === 'text').map(p => p.text).join('');
  const [preview, setPreview] = useState<Preview>();
  const user = m.role === 'user';
  return <MessagePrimitive.Root className={`message ${user ? 'user' : 'assistant'}`} aria-label={user ? '用户消息' : '助手回复'} data-message-id={m.id} onContextMenu={event => menu(event, {kind: 'message', text, evidence: collectEvidence(host?.activities)})}>
    <div className="message-content">
      {host && <ProcessDetails message={host}/>}
      {host?.status && !['complete', 'running'].includes(host.status) && !host.activities?.length && !host.results?.length && <div className="message-status"><span className={`badge ${host.status === 'failed' ? 'bad' : ''}`}>{statusLabel(host.status)}</span></div>}
      {host?.results?.length && host.result_content ? <section className="published-results" aria-label="已保存的工具结果"><Markdown text={host.result_content} rpc={store.rpc} onError={store.report}/>{host.status === 'running' && <p role="status">结果已保存，正在完成后续分析…</p>}</section> : null}
      <Markdown text={host?.results?.length && host.result_content ? text.replace(host.result_content,'').trim() : text} rpc={store.rpc} onError={store.report} smooth={smooth && host?.status === 'running'}/>
      {host?.status === 'running' && !text && !host.activities?.length && <div className="actual-running"><LoaderCircle size={15}/>任务运行中，等待后端内容…</div>}
      {host?.attachments?.length ? <div className="attachment-list">{host.attachments.map(a => <button className="attachment-chip" key={a.id} onClick={() => void store.rpc('attachments.preview', {attachmentId: a.id}).then(setPreview).catch(store.report)}><FileText size={14}/>{a.name}<small>{(a.size / 1024).toFixed(1)} KB</small></button>)}</div> : null}
      {host?.followup_actions?.length && host.taskId && host.status !== 'running' ? <div className="actions">{host.followup_actions.map(mode=><button key={mode} disabled={Boolean(store.running(sessionId))} title="把一句可修改的请求填进输入框，发不发由你决定" onClick={()=>{draft(mode,host.taskId!)}}>{{L2:'解释结果',L3:'分析原材料',L4:'通用讨论'}[mode]}</button>)}</div> : null}
      {text && <div className="message-actions"><IconButton label="复制消息" onClick={() => void navigator.clipboard.writeText(text).catch(store.report)}><Copy size={14}/></IconButton></div>}
    </div>
    {preview && <AttachmentPreview preview={preview} onClose={() => setPreview(undefined)}/>}
  </MessagePrimitive.Root>;
}
export function Chat({store, session, onSettings}: {store: ChatStore; session: Session; onSettings: () => void}) {
  const state = useChatState(store); const runtimeRef = useRef<AssistantRuntime>(null); const viewport = useRef<HTMLDivElement>(null); const content = useRef<HTMLDivElement>(null);
  // 续接按钮只草拟一句可改的请求，不替用户发送；文字改过或加了附件就按新任务提交。
  const pendingFollowup = useRef<FollowupDraft | null>(null);
  const draft = (mode: 'L2' | 'L3' | 'L4', taskId: string) => {
    const next = draftFollowup(session.id, taskId, mode);
    pendingFollowup.current = next;
    runtimeRef.current?.thread.composer.setText(next.text);
  };
  const conversation = useStoredWidth(store, 'conversation_width', resolveConversationWidth);
  const disclosure = useDisclosureAnchor(viewport, content);
  const initialScroll = useRef(session.scroll); const initialAnchor = useRef(session.anchor || null); const [restored, setRestored] = useState(false);
  const savedAttachments = useRef([...(store.attachments.get(session.id) || [])]);
  const boot = state.boot!; const modelConfig = boot.settings.api.enabled ? boot.settings.api : boot.settings.local;
  const unknownAuto = modelConfig.token_mode === 'auto' && !knownModel(boot.models, modelConfig);
  const [menu, setMenu] = useState<TranscriptMenuState | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const messages = state.messages[session.id] || [];
  const [windowSize, setWindowSize] = useState(TRANSCRIPT_WINDOW_MIN); const [hydrated, setHydrated] = useState(false); const [searchOpen, setSearchOpen] = useState(false);
  const rows = useRef<RowOffset[]>([]); const pending = useRef<{height: number; at: number} | null>(null); const own = useRef({top: -1, at: 0});
  // The reading anchor has to stay inside the mounted window, or restoring it is impossible.
  const anchorIndex = restored || !initialAnchor.current ? -1 : messages.findIndex(message => message.id === initialAnchor.current!.messageId);
  const required = anchorIndex === -1 ? 0 : messages.length - anchorIndex;
  const windowState = reduceTranscriptWindow({historyLength: messages.length, windowSize: Math.max(windowSize, required), initialCommit: !hydrated && required === 0});
  // Memoised on the inputs that actually change it: a fresh array every render would
  // defeat the runtime's own memoisation and rebuild the marker list with it.
  const mountedMessages = useMemo(() => windowState.bounded ? messages.slice(messages.length - windowState.mounted) : messages, [messages, windowState.bounded, windowState.mounted]);
  const markers = useMemo(() => conversationMarkers(mountedMessages), [mountedMessages]);
  const copy = (payload: string) => { if (payload) void navigator.clipboard.writeText(payload).catch(store.report); };
  // One floating layer per conversation: a row cannot own a menu that outlives a re-render.
  const openMenu = (event: ReactMouseEvent<HTMLElement>, request: TranscriptMenuRequest) => {
    event.preventDefault();
    const trigger = event.currentTarget;
    const entries = request.kind === 'message'
      ? messageMenuEntries({text: request.text, hasEvidence: request.evidence.length > 0})
      : conversationMenuEntries({hasMessages: messages.some(message => message.content.trim())});
    if (!entries.length) { setMenu(null); return; }
    const run = (action: TranscriptMenuAction) => {
      if (action === 'copy-message') return copy(messageCopyPayload(trigger, request.kind === 'message' ? request.text : ''));
      if (action === 'select-message') return selectContents(trigger.querySelector('.message-content'));
      if (action === 'copy-evidence') return copy(evidencePayload(request.kind === 'message' ? request.evidence : []));
      if (action === 'copy-conversation') return copy(transcriptText(messages));
      if (action === 'select-conversation') return selectContents(content.current);
      // Instant, like PI-Desktop's own conversation menu: a smooth animation is cancelled
      // when the menu unmounts in the same tick, so it would silently do nothing.
      if (action === 'scroll-top') { const node = viewport.current; if (node) { node.scrollTo({top: 0, behavior: 'auto'}); own.current = {top: 0, at: performance.now()}; } return; }
      return returnToLatest(viewport.current);
    };
    setMenu({label: request.kind === 'message' ? '消息操作' : '会话操作', point: {x: event.clientX + 4, y: event.clientY + 4}, trigger, items: transcriptMenuItems(entries, run)});
  };
  const attachmentsAdapter = useMemo<AttachmentAdapter>(() => ({
    accept: '*',
    add: async ({file}) => { const a = await importFile(store.rpc, file); return {...attachmentDescriptor(a), file, status: {type: 'requires-action', reason: 'composer-send'}}; },
    remove: async () => { /* Contract has no deletion RPC: detach only; storage remains host-owned. */ },
    send: async a => ({...a, file: undefined, content: [], status: {type: 'complete'}}),
  }), [store]);
  const runtime = useExternalStoreRuntime<Message>({
    messages: mountedMessages, convertMessage,
    isRunning: Boolean(store.running(session.id)), isLoading: Boolean(state.loading[session.id]),
    isDisabled: readOnly(session)||Boolean(session.archived), isSendDisabled: !state.connected || Boolean(state.submitting[session.id]) || unknownAuto,
    adapters: {attachments: attachmentsAdapter},
    onNew: async message => {
      const text = message.content.filter(p => p.type === 'text').map(p => p.text).join('');
      const attachments = message.attachments || [];
      const next = followupContinuation(pendingFollowup.current, session.id, text, attachments.length);
      pendingFollowup.current = null;
      try {
        // 草稿原样发回才算续接；改了文字或加了附件就是新任务（与 Tk 版 pending_analysis 同一语义）。
        if (next) {
          await store.rpc('tasks.followup', {sessionId: session.id, sourceTaskId: next.taskId, mode: next.mode});
          await store.pollOnce();
          return;
        }
        await store.submit(session.id, text, attachments.map(a => a.id));
      }
      catch (e) {
        const composer = runtimeRef.current?.thread.composer;
        composer?.setText(text);
        for (const a of attachments) if (!composer?.getState().attachments.some(current => current.id === a.id)) await composer?.addAttachment({id: a.id, type: a.type, name: a.name, contentType: a.contentType, content: []});
        throw e;
      }
    },
    onCancel: () => store.cancel(session.id).catch(store.report),
  });
  runtimeRef.current = runtime;
  useEffect(() => {
    const initialAttachments = savedAttachments.current;
    runtime.thread.composer.setText(session.draft || '');
    for (const a of initialAttachments) void runtime.thread.composer.addAttachment(attachmentDescriptor(a)).catch(store.report);
    let lastText = runtime.thread.composer.getState().text;
    const unsubscribe = runtime.thread.composer.subscribe(() => {
      const text = runtime.thread.composer.getState().text;
      if (text !== lastText && !store.getSnapshot().submitting[session.id]) { lastText = text; store.view(session.id, {draft: text}); }
    });
    return () => { unsubscribe(); store.flushView(session.id); };
  }, [runtime, session.id, store]);
  // Mod+K matches PI-Desktop's own `openSearch` binding; Ctrl+F stays with the host's find bar.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // PI-Desktop useAppShellRuntime.tsx: composition belongs to the IME.
      if (event.isComposing || event.keyCode === 229) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLocaleLowerCase() === 'k') { event.preventDefault(); setSearchOpen(true); return; }
      if (event.key === 'Escape') setSearchOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  /** Row tops in content coordinates: measured on layout changes, read by binary search while scrolling. */
  const measureRows = useCallback(() => {
    const node = viewport.current; const contentNode = content.current;
    if (!node || !contentNode) return;
    const base = node.scrollTop; const top = node.getBoundingClientRect().top;
    rows.current = Array.from(contentNode.querySelectorAll<HTMLElement>('[data-message-id]')).map(el => ({id: el.dataset.messageId || '', top: el.getBoundingClientRect().top - top + base}));
  }, []);
  /**
   * Growing the window prepends rows above the reading position, so the scroller has to
   * absorb exactly that much height. This runs from the content observer rather than from a
   * render effect: the message list can land in the DOM after this commit (the runtime syncs
   * it on its own schedule), so measuring during the commit reads the old height and silently
   * skips the compensation. A page can also arrive over several deliveries, so the record
   * stays live briefly and each delivery tops the scroll up by whatever it added.
   */
  const settlePrepend = useCallback(() => {
    const node = viewport.current; const current = pending.current;
    if (!node || !current) return;
    const delta = prependedHeight(current.height, node.scrollHeight);
    if (delta > 0) {
      node.scrollTop += delta;
      own.current = {top: node.scrollTop, at: performance.now()};
      current.height = node.scrollHeight;
      current.at = performance.now();
    }
  }, []);
  useLayoutEffect(() => {
    const node = content.current;
    if (!node || typeof ResizeObserver === 'undefined') return;
    const sync = () => { settlePrepend(); measureRows(); };
    sync();
    const observer = new ResizeObserver(sync); observer.observe(node, {box: 'border-box'});
    return () => observer.disconnect();
  }, [measureRows, settlePrepend]);
  // Once it has been idle long enough, the record must stop claiming unrelated later growth.
  useEffect(() => {
    const timer = setInterval(() => { const current = pending.current; if (current && performance.now() - current.at > 400) pending.current = null; }, 200);
    return () => clearInterval(timer);
  }, []);
  // Two-phase hydrate: paint the tail of the history first, then widen to the steady window.
  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => setHydrated(true));
    return () => cancelAnimationFrame(frame);
  }, [session.id]);
  // The requirement has to outlive the restore: once `restored` flips, an uncommitted
  // window would shrink back to the steady minimum and drop the anchored row.
  useEffect(() => { if (required) setWindowSize(current => Math.max(current, required)); }, [required]);
  const revealEarlier = () => {
    const node = viewport.current;
    if (node) pending.current = {height: node.scrollHeight, at: performance.now()};
    setWindowSize(current => growTranscriptWindow(Math.max(current, windowState.mounted), messages.length));
  };
  useLayoutEffect(() => {
    if (state.loading[session.id] || restored || !viewport.current) return;
    const node = viewport.current;
    measureRows();
    // The anchor is durable across windowing; the pixel offset is only a pre-anchor fallback.
    const target = initialAnchor.current ? scrollTopFor(rows.current, initialAnchor.current) : null;
    const start = target ?? initialScroll.current;
    node.scrollTop = start;
    own.current = {top: start, at: performance.now()};
    const frame = requestAnimationFrame(() => { node.scrollTop = start; own.current = {top: start, at: performance.now()}; setRestored(true); });
    return () => cancelAnimationFrame(frame);
  }, [measureRows, state.loading[session.id], restored]);
  return <Context.Provider value={{store, sessionId:session.id, smooth: Boolean(boot.settings.preferences.smooth_stream), disclosure, menu: openMenu, draft}}><AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root className="thread" style={{'--conversation-width': `${conversation.width}px`} as CSSProperties}><header className="chat-header" onPointerDown={event => { if (event.button !== 0) return; const target = event.target as HTMLElement; if (target.closest('button, a, input, select, textarea, [contenteditable]')) return; void store.rpc('window.drag', {}).catch(() => {}); }}><div><h1><Folder size={14} aria-hidden="true"/><span className="chat-title-text" title={session.title || '新会话'}>{session.title || '新会话'}</span>{readOnly(session) && <span className="badge">旧记录 · 只读</span>}</h1></div><div className="actions"><IconButton label={searchOpen ? '关闭会话内查找' : '在会话中查找（Ctrl / Cmd + K）'} aria-pressed={searchOpen} onClick={() => setSearchOpen(open => !open)}><Search size={17}/></IconButton><IconButton label="刷新当前会话" onClick={() => void store.load(session.id).catch(store.report)}><RefreshCw size={17}/></IconButton></div></header>{session.archived && <div className="archived-conversation"><span>此会话已归档</span><button disabled={state.organizationBusy} onClick={() => void store.archive(session.id,false).catch(store.report)}>恢复会话</button></div>}{session.forkedFrom && <div className="fork-origin">分叉自 {session.forkedFromTitle || session.forkedFrom}</div>}{searchOpen && <ChatSearch content={content} viewport={viewport} hidden={messages.slice(0, Math.max(0, messages.length - windowState.mounted))} hiddenCount={windowState.hiddenAbove} onRevealEarlier={revealEarlier} onAlign={top => { own.current = {top, at: performance.now()}; }} onClose={() => setSearchOpen(false)}/>}{unknownAuto && <div className="capability-banner"><CircleAlert size={16}/><span>模型窗口未知，自动预算不能猜测。</span><button onClick={onSettings}>配置真实窗口 / 手动预算</button></div>}<div className="transcript-shell" onContextMenu={event => { if (!event.defaultPrevented) openMenu(event, {kind: 'conversation'}); }}><ThreadPrimitive.Viewport ref={viewport} className="transcript" autoScroll={restored} scrollToBottomOnInitialize={false} scrollToBottomOnThreadSwitch={false} scrollToBottomOnRunStart={false} onScroll={e => { const node = e.currentTarget; if (!restored) return; const mine = Math.abs(node.scrollTop - own.current.top) < 2 && performance.now() - own.current.at < 600; if (!mine && rows.current.length) store.view(session.id, {scroll: node.scrollTop, anchor: anchorFor(rows.current, node.scrollTop) ?? undefined}); if (!mine && node.scrollTop < HISTORY_REVEAL_THRESHOLD_PX && windowState.hiddenAbove > 0) revealEarlier(); }} aria-label="会话消息">
    {windowState.hiddenAbove > 0 && <div className="history-reveal"><button type="button" onClick={revealEarlier}>显示更早的 {windowState.hiddenAbove} 条消息</button></div>}<div className="transcript-content" ref={content}><ThreadPrimitive.Messages components={{Message: MessageCard}}/></div>
  </ThreadPrimitive.Viewport><ThreadPrimitive.Empty><div className="welcome"><div className="welcome-mark"><img src={praatIcon} alt=""/></div><h2>我们应该做些什么</h2><p>调动原生Praat工具测量计算或使用LLM获得纠音建议</p><div className="welcome-cards">{['分析音高与时长','计算VOT','获得纠音建议'].map(text => <button key={text} onClick={() => { runtime.thread.composer.setText(text === '获得纠音建议' ? '与标准音对比，指出发音不足之处与待改进点' : text); }}>{text}</button>)}</div></div></ThreadPrimitive.Empty><ConversationMinimap markers={markers} rows={rows} viewport={viewport} hiddenAbove={windowState.hiddenAbove} onRevealEarlier={revealEarlier}/><ConversationWidthHandles viewport={viewport} width={conversation.width} onPreview={conversation.preview} onCommit={conversation.commit}/></div><div className="jump-wrap"><ThreadPrimitive.ScrollToBottom className="jump-button" aria-label="回到最新消息"><ArrowDown size={16}/>回到最新</ThreadPrimitive.ScrollToBottom></div><Composer store={store} session={session} composer={runtime.thread.composer} onSettings={onSettings}/><ContextMenu state={menu} onClose={closeMenu}/></ThreadPrimitive.Root></AssistantRuntimeProvider></Context.Provider>;
}
