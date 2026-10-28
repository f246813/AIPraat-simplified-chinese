import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps, type CSSProperties, type MouseEvent as ReactMouseEvent } from 'react';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useAuiState, ThreadPrimitive, MessagePrimitive, type AssistantRuntime, type AttachmentAdapter, type ThreadMessageLike } from '@assistant-ui/react';
import { ArrowDown, Brain, CircleAlert, Copy, FileText, Folder, LoaderCircle, RefreshCw, Search, Shield, Wrench } from 'lucide-react';
import praatIcon from '../../../icon.png';
import { Markdown } from './Markdown';
import { Composer, AttachmentPreview, attachmentDescriptor } from './Composer';
import { importFile, safePreviewUrl } from './bridge';
import { ChatStore, shallowEqual, useChatSelector } from './store';
import { knownModel, readOnly, type Activity, type Attachment, type Message, type Preview, type ReadingAnchor, type Rpc, type Session } from './types';
import { IconButton, Json, statusLabel } from './ui';
import { ConversationWidthHandles, useStoredWidth } from './Panes';
import { ContextMenu } from './pi/ContextMenu';
import { messageCopyPayload, selectContents, transcriptMenuItems, type TranscriptMenuState } from './TranscriptMenu';
import { collectEvidence, conversationMenuEntries, evidencePayload, messageMenuEntries, transcriptText, type EvidenceEntry, type TranscriptMenuAction } from './transcript-menu';
import { resolveConversationWidth } from './layout';
import { growTranscriptWindow, logicalStartIndex, reduceTranscriptWindow, selectionPinSpan, TRANSCRIPT_WINDOW_MIN } from './transcript-window';
import { VirtualTranscript, type TranscriptHandle } from './VirtualTranscript';
import { ConversationMinimap } from './MinimapRail';
import { conversationMarkers } from './minimap';
import { ChatSearch } from './SearchBar';
import { activityState } from './codex/activity-state';
/** A menu row publishes only the data it owns; the transcript owns the single floating layer. */
type TranscriptMenuRequest = {kind: 'message'; text: string; evidence: EvidenceEntry[]} | {kind: 'conversation'};
const Context = createContext<{store: ChatStore; smooth: boolean; liveTasks: Set<string>; seen: Set<string>; disclosures: Map<string, boolean>; holdDisclosure: (summary: HTMLElement) => void; menu: (event: ReactMouseEvent<HTMLElement>, request: TranscriptMenuRequest) => void}>(null!);
function Disclosure({disclosureId, children, ...props}: ComponentProps<'details'> & {disclosureId: string}) {
  const {disclosures, holdDisclosure} = useContext(Context);
  const [open, setOpen] = useState(() => disclosures.get(disclosureId) ?? false);
  return <details {...props} open={open} onToggle={event => {
    const next = event.currentTarget.open;
    disclosures.set(disclosureId, next);
    setOpen(next);
  }} onClickCapture={event => {
    const summary = (event.target as Element).closest<HTMLElement>('summary');
    if (summary?.parentElement === event.currentTarget) holdDisclosure(summary);
  }}>{children}</details>;
}
/** Stable component mapping for the message rows: a fresh object each render would defeat the row's own memoisation. */
const MESSAGE_COMPONENTS = {Message: MessageCard};
/**
 * One mounted row, addressed by its index in the full loaded history.
 *
 * The row cannot assume the runtime's converted list and the host array are always equal: a
 * freshly opened session mounts its tail rows before the external-store runtime has published the
 * messages they point at. `MessageByIndex` reads the runtime's own list, so the row checks that the
 * runtime entry at that index carries the expected message id and renders nothing until it does —
 * never an out-of-bounds lookup, never a row showing a different message.
 */
function TranscriptMessage({index, messageId}: {index: number; messageId: string}) {
  const ready = useAuiState(state => state.thread.messages[index]?.id === messageId);
  return ready ? <ThreadPrimitive.MessageByIndex index={index} components={MESSAGE_COMPONENTS}/> : null;
}
export function convertMessage(message: Message): ThreadMessageLike {
  const status = message.status === 'running' ? {type: 'running' as const} : message.status === 'failed' ? {type: 'incomplete' as const, reason: 'error' as const} : ['partial','cancelled','interrupted'].includes(message.status || '') ? {type: 'incomplete' as const, reason: 'cancelled' as const} : {type: 'complete' as const, reason: 'stop' as const};
  return {id: message.id, role: message.role, content: message.content, ...(message.role === 'assistant' ? {status} : {}), metadata: {custom: {host: message}}, ...(message.role === 'user' ? {attachments: message.attachments?.map(a => ({...attachmentDescriptor(a), status: {type: 'complete' as const}}))} : {})};
}
function ActivityCard({activity, messageId}: {activity: Activity; messageId: string}) {
  const {store} = useContext(Context);
  const tool = activity.type === 'tool'; const thinking = activity.type === 'thinking';
  const Icon = tool ? Wrench : thinking ? Brain : activity.type === 'compression' ? RefreshCw : LoaderCircle;
  if (thinking && !activity.text) return null;
  // `<details>` toggles natively; the virtual engine's per-row measurement absorbs the height
  // change above or below the reading position, so no separate anchor writer is needed here.
  return <Disclosure disclosureId={`${messageId}:activity:${activity.id}`} className={`activity ${tool ? 'tool-card' : ''}`} data-activity-id={activity.id}><summary><Icon size={15}/><span>{tool ? activity.name || '工具执行' : thinking ? '模型思考' : activity.type === 'compression' ? '上下文压缩' : '执行进度'}</span><span className={`badge ${activity.status === 'failed' ? 'bad' : ''}`}>{statusLabel(activity.status)}</span></summary><div className="activity-body">{activity.execution && <p className="execution-fact"><Shield size={14}/>投递 / 执行事实：<strong>{activity.execution}</strong></p>}{activity.text && <Markdown text={activity.text} rpc={store.rpc} onError={store.report} streaming={activity.status === 'running'}/>} {activity.args !== undefined && <><h5>参数</h5><Json value={activity.args}/></>}{activity.result !== undefined && <><h5>结果 / 结构化证据</h5><Json value={activity.result}/></>}{tool && !activity.execution && <p className="muted">未收到操作执行记录</p>}</div></Disclosure>;
}
/** One disclosure per reply; streaming updates never force it open. */
function ProcessDetails({message}: {message: Message}) {
  const activities = (message.activities || []).filter(a => a.type !== 'thinking' || a.text);
  const results = message.results || [];
  if (!activities.length && !results.length) return null;
  const running = message.status === 'running';
  const failed = activities.some(a => a.status === 'failed' || a.status === 'error') || results.some(r => r.result_valid === false || r.status === 'failed' || r.status === 'error');
  const state = activityState(failed, running);
  const status = running ? '执行中' : message.status && message.status !== 'complete' ? statusLabel(message.status) : '执行过程';
  return <Disclosure disclosureId={`${message.id}:process`} className="process-details" data-process-state={state}>
    <summary>
      {running && <LoaderCircle size={13} aria-hidden="true"/>}
      <span>{status}</span>
      <span className="process-count">{activities.length ? `${activities.length} 个步骤` : `${results.length} 项工具记录`}</span>
      {failed && <span className="process-failure">含失败步骤</span>}
    </summary>
    <div className="process-body">
      {activities.length > 0 && <div className="activities">{activities.map(a => <ActivityCard key={a.id} activity={a} messageId={message.id}/>)}</div>}
      {results.length > 0 && <section className="process-results" aria-label="工具执行记录">{results.map(fact => <Disclosure key={fact.id} disclosureId={`${message.id}:result:${fact.id}`}><summary>{fact.tool} · {fact.result_valid === false ? '结果不可用' : statusLabel(fact.status)} · {fact.execution || '宿主未报告投递状态'}</summary><Json value={fact}/></Disclosure>)}</section>}
    </div>
  </Disclosure>;
}
function ReportImage({attachment, rpc}: {attachment: Attachment; rpc: Rpc}) {
  const [preview, setPreview] = useState<Preview>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void rpc('attachments.preview', {attachmentId: attachment.id}).then(value => {if (active) setPreview(value);})
      .catch(reason => {if (active) setError(String(reason instanceof Error ? reason.message : reason));});
    return () => {active = false;};
  }, [attachment.id, rpc]);
  const url = safePreviewUrl(preview?.dataUrl);
  return <figure className="report-figure" aria-label="纠音叠加图">{url ? <img src={url} alt="标准音与学习者录音的声学对比叠加图"/> : <p role="status">{error || '正在读取叠加图…'}</p>}<figcaption>{attachment.name}</figcaption></figure>;
}
function MessageCard() {
  const {store, smooth, liveTasks, seen, menu} = useContext(Context); const m = useAuiState(s => s.message);
  const firstMount = useRef(!seen.has(m.id));
  useEffect(() => { seen.add(m.id); }, [m.id, seen]);
  const host = m.metadata.custom.host as Message | undefined;
  const text = host?.content ?? m.content.filter(p => p.type === 'text').map(p => p.text).join('');
  const [preview, setPreview] = useState<Preview>();
  const user = m.role === 'user';
  const live = !user && Boolean(host?.taskId && liveTasks.has(host.taskId));
  return <MessagePrimitive.Root className={`message ${user ? 'user' : 'assistant'}`} aria-label={user ? '用户消息' : '助手回复'} data-message-id={m.id} onContextMenu={event => menu(event, {kind: 'message', text, evidence: collectEvidence(host?.activities)})}>
    <div className="message-content">
      {host && <ProcessDetails message={host}/>}
      {host?.status && !['complete', 'running'].includes(host.status) && !host.activities?.length && !host.results?.length && <div className="message-status"><span className={`badge ${host.status === 'failed' ? 'bad' : ''}`}>{statusLabel(host.status)}</span></div>}
      {host?.results?.length && host.result_content ? <section className="published-results" aria-label="已保存的工具结果"><Markdown text={host.result_content} rpc={store.rpc} onError={store.report}/>{host.status === 'running' && <p role="status">结果已保存，正在完成后续分析…</p>}</section> : null}
      <Markdown text={host?.results?.length && host.result_content ? text.replace(host.result_content,'').trim() : text} rpc={store.rpc} onError={store.report} streaming={!user && host?.status === 'running'} smooth={smooth && !user && (host?.status === 'running' || live && host?.status === 'complete')} fromStart={live && firstMount.current}/>
      {host?.report?.type === 'pronunciation' && host.attachments?.filter(a => a.mime.startsWith('image/')).map(a => <ReportImage key={a.id} attachment={a} rpc={store.rpc}/>)}
      {host?.status === 'running' && !text && !host.activities?.length && <div className="actual-running"><LoaderCircle size={15}/>任务运行中，等待后端内容…</div>}
      {host?.attachments?.length ? <div className="attachment-list">{host.attachments.map(a => <button className="attachment-chip" key={a.id} onClick={() => void store.rpc('attachments.preview', {attachmentId: a.id}).then(setPreview).catch(store.report)}><FileText size={14}/>{a.name}<small>{(a.size / 1024).toFixed(1)} KB</small></button>)}</div> : null}
      {text && <div className="message-actions"><IconButton label="复制消息" onClick={() => void navigator.clipboard.writeText(text).catch(store.report)}><Copy size={14}/></IconButton></div>}
    </div>
    {preview && <AttachmentPreview preview={preview} onClose={() => setPreview(undefined)}/>}
  </MessagePrimitive.Root>;
}
export function Chat({store, session, onSettings}: {store: ChatStore; session: Session; onSettings: () => void}) {
  // Narrow to this session's messages plus the loading/submit flags and task map the
  // body needs. A background session's text increment changes none of these fields.
  const state = useChatSelector(store, s => ({boot: s.boot, tasks: s.tasks, messages: s.messages[session.id], loading: s.loading[session.id], submitting: s.submitting[session.id], connected: s.connected, organizationBusy: s.organizationBusy}), shallowEqual); const runtimeRef = useRef<AssistantRuntime>(null); const viewport = useRef<HTMLDivElement>(null); const transcript = useRef<TranscriptHandle>(null);
  const conversation = useStoredWidth(store, 'conversation_width', resolveConversationWidth);
  // Restore from the per-session view snapshot, not the reactive session array: a
  // draft typed after the last host load lives only here, and reading it imperatively
  // keeps the body off the hot path that would otherwise rebuild the sidebar.
  const viewSnapshot = store.getView(session.id);
  const initialScroll = useRef(viewSnapshot.scroll ?? session.scroll); const initialAnchor = useRef((viewSnapshot.anchor ?? session.anchor) || null); const [restored, setRestored] = useState(false); const [following, setFollowing] = useState(true);
  const savedAttachments = useRef([...(store.attachments.get(session.id) || [])]);
  const boot = state.boot!; const modelConfig = boot.settings.api.enabled ? boot.settings.api : boot.settings.local;
  const unknownAuto = modelConfig.token_mode === 'auto' && !knownModel(boot.models, modelConfig);
  const [menu, setMenu] = useState<TranscriptMenuState | null>(null);
  const initialTasks = useRef(new Set(Object.keys(state.tasks)));
  const liveTasks = new Set(Object.keys(state.tasks).filter(id => !initialTasks.current.has(id)));
  const messages = state.messages || [];
  const seenMessages = useRef(new Set<string>());
  const disclosures = useRef(new Map<string, boolean>());
  const openedFirst = useRef<string | null>(null);
  const [windowSize, setWindowSize] = useState(TRANSCRIPT_WINDOW_MIN); const [hydrated, setHydrated] = useState(false); const [searchOpen, setSearchOpen] = useState(false);
  // Rows an interaction holds mounted: the open menu's target, the row that owns focus, and the rows a
  // live text selection spans. They arrive as one set because the mount is a set, not three overlays.
  const [pinned, setPinned] = useState<ReadonlySet<string>>(() => new Set());
  const menuPinned = useRef<string | null>(null);
  const focusPinned = useRef<string | null>(null);
  const selectionPinned = useRef<string[]>([]);
  const selectionLimit = useRef<{id: string; forward: boolean} | null>(null);
  const pinsKey = useRef('');
  const messagesRef = useRef(messages); messagesRef.current = messages;
  // The durable anchor has to sit inside the logical range, or it cannot be reached: widen the
  // range to it before the first commit so the restore target is a real row, not an estimate gap.
  const anchorIndex = restored || !initialAnchor.current ? -1 : messages.findIndex(message => message.id === initialAnchor.current!.messageId);
  const openedIndex = useMemo(() => openedFirst.current ? messages.findIndex(message => message.id === openedFirst.current) : -1, [messages]);
  const required = Math.max(anchorIndex === -1 ? 0 : messages.length - anchorIndex, openedIndex === -1 ? 0 : messages.length - openedIndex);
  const windowState = reduceTranscriptWindow({historyLength: messages.length, windowSize: Math.max(windowSize, required), initialCommit: !hydrated && required === 0});
  const logicalStart = logicalStartIndex(messages.length, windowState.mounted);
  useLayoutEffect(() => { if (hydrated) openedFirst.current = messages[logicalStart]?.id ?? null; }, [hydrated, messages, logicalStart]);
  // The rail describes the expanded logical history, not the handful of rows the viewport
  // happens to mount, so it reads the logical slice while positions come from engine metadata.
  const logicalMessages = useMemo(() => messages.slice(logicalStart), [messages, logicalStart]);
  const markers = useMemo(() => conversationMarkers(logicalMessages), [logicalMessages]);
  const copy = (payload: string) => { if (payload) void navigator.clipboard.writeText(payload).catch(store.report); };
  const applyPins = useCallback(() => {
    const next = [...selectionPinned.current];
    if (focusPinned.current && !next.includes(focusPinned.current)) next.push(focusPinned.current);
    if (menuPinned.current && !next.includes(menuPinned.current)) next.push(menuPinned.current);
    const key = next.join('|');
    if (key === pinsKey.current) return;
    pinsKey.current = key;
    setPinned(new Set(next));
  }, []);
  // A browser text selection is real DOM between the anchor and the focus, so recycling a row the
  // reader is dragging through would silently cut the selection; focus is the same story. The span is
  // capped by the selection budget, and releasing the interaction releases the rows back to the engine.
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const rowId = (from: Node | null): string | null =>
      ((from instanceof Element ? from : from?.parentElement)?.closest('[data-message-id]') as HTMLElement | null)?.dataset.messageId ?? null;
    const onSelectionChange = () => {
      const selection = window.getSelection?.();
      const ours = Boolean(selection && !selection.isCollapsed && selection.rangeCount
        && node.contains(selection.getRangeAt(0).commonAncestorContainer));
      const ids = messagesRef.current.map(message => message.id);
      const from = ours ? ids.indexOf(rowId(selection!.anchorNode) ?? '') : -1;
      const to = ours ? ids.indexOf(rowId(selection!.focusNode) ?? '') : -1;
      if (from < 0 || to < 0) { selectionPinned.current = []; selectionLimit.current = null; }
      else {
        const [start, end] = selectionPinSpan(from, to);
        const next = ids.slice(start, end + 1);
        if (to < start || to > end) {
          const id = ids[to < start ? start : end];
          const boundary = node.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(id)}"]`);
          if (boundary) selection!.extend(boundary, to < start ? 0 : boundary.childNodes.length);
          else {
            selectionLimit.current = {id, forward: to > end};
            next.push(ids[to]);
          }
        }
        selectionPinned.current = next;
      }
      applyPins();
    };
    const onFocusIn = (event: FocusEvent) => { focusPinned.current = rowId(event.target as Node | null); applyPins(); };
    const onFocusOut = (event: FocusEvent) => {
      if (event.relatedTarget && node.contains(event.relatedTarget as Node)) return;
      focusPinned.current = null; applyPins();
    };
    document.addEventListener('selectionchange', onSelectionChange);
    node.addEventListener('focusin', onFocusIn);
    node.addEventListener('focusout', onFocusOut);
    return () => {
      document.removeEventListener('selectionchange', onSelectionChange);
      node.removeEventListener('focusin', onFocusIn);
      node.removeEventListener('focusout', onFocusOut);
      selectionPinned.current = []; focusPinned.current = null;
    };
  }, [applyPins]);
  useLayoutEffect(() => {
    const limit = selectionLimit.current;
    if (!limit) return;
    const boundary = viewport.current?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(limit.id)}"]`);
    if (!boundary) return;
    selectionLimit.current = null;
    window.getSelection()?.extend(boundary, limit.forward ? boundary.childNodes.length : 0);
  }, [pinned]);
  // The batched reading position: the engine already collapsed the scroll burst into one frame,
  // so this only merges the anchor into the per-session snapshot (never rebuilds the sidebar).
  const handleAnchor = useCallback((anchor: ReadingAnchor | null, scrollTop: number) => { if (restored) store.view(session.id, {scroll: scrollTop, anchor: anchor ?? undefined}); }, [restored, session.id, store]);
  const flushView = useCallback(() => { void store.flushView(session.id); }, [session.id, store]);
  // "Following" is the reading position being at the newest row: streaming can then be followed and
  // no jump affordance is owed. The engine reports the flag from the same per-frame batch that reads
  // the anchor, and it only changes when the newest row leaves or enters the rendered slice.
  const handleFollowChange = useCallback((atEnd: boolean) => setFollowing(atEnd), []);
  const revealEarlier = useCallback(() => setWindowSize(current => growTranscriptWindow(Math.max(current, messages.length - logicalStart), messages.length)), [logicalStart, messages.length]);
  // Opening the range until a target index is included; the engine re-fires its navigation once
  // the row is inside, so a rail click or search jump into withheld history lands after expansion.
  const revealToIndex = useCallback((index: number) => setWindowSize(current => Math.max(current, TRANSCRIPT_WINDOW_MIN, messages.length - index)), [messages.length]);
  // The search panel reads the rendered list and reaches a hit only through the positioning entry.
  const transcriptElement = useCallback(() => transcript.current?.element() ?? null, []);
  const viewportElement = useCallback(() => viewport.current, []);
  const locateMessage = useCallback((messageId: string, resolveOffset?: () => number | null) => transcript.current?.position(messageId, Math.min(160, (viewport.current?.clientHeight ?? 480) / 3), {resolveOffset}), []);
  const rowAtOffset = useCallback((offset: number) => transcript.current?.rowAtOffset(offset) ?? null, []);
  const subscribeViewport = useCallback((listener: () => void) => transcript.current?.subscribe(listener) ?? (() => {}), []);
  const holdDisclosure = useCallback((summary: HTMLElement) => {
    const row = summary.closest<HTMLElement>('[data-message-id]');
    const node = viewport.current;
    if (!row?.dataset.messageId || !node) return;
    transcript.current?.position(row.dataset.messageId, summary.getBoundingClientRect().top - node.getBoundingClientRect().top, {
      resolveOffset: () => summary.isConnected ? summary.getBoundingClientRect().top - row.getBoundingClientRect().top : null,
    });
  }, []);
  // One floating layer per conversation: a row cannot own a menu that outlives a re-render.
  const openMenu = (event: ReactMouseEvent<HTMLElement>, request: TranscriptMenuRequest) => {
    event.preventDefault();
    const trigger = event.currentTarget;
    const messageId = trigger.closest<HTMLElement>('[data-message-id]')?.dataset.messageId || '';
    const entries = request.kind === 'message'
      ? messageMenuEntries({text: request.text, hasEvidence: request.evidence.length > 0})
      : conversationMenuEntries({hasMessages: messages.some(message => message.content.trim())});
    if (!entries.length) { setMenu(null); return; }
    const run = (action: TranscriptMenuAction) => {
      if (action === 'copy-message') return copy(messageCopyPayload(trigger, request.kind === 'message' ? request.text : ''));
      if (action === 'select-message') return selectContents(trigger.querySelector('.message-content'));
      if (action === 'copy-evidence') return copy(evidencePayload(request.kind === 'message' ? request.evidence : []));
      if (action === 'copy-conversation') return copy(transcriptText(messages));
      // Virtualisation means the DOM only ever holds the mounted window, so the menu can only
      // select what is actually shown; full-session copy stays on the data path above.
      if (action === 'select-conversation') return selectContents(transcript.current?.element() ?? null);
      // The start of the conversation, not merely the start of what happens to be expanded: the same
      // positioning entry opens the logical range far enough and lands on the first message row.
      if (action === 'scroll-top') { const first = messages[0]; if (first) transcript.current?.position(first.id, 0); return; }
      transcript.current?.scrollToLatest();
    };
    // Pin the row so its DOM survives until the menu action reads it, then release on close.
    if (messageId && request.kind === 'message') { menuPinned.current = messageId; applyPins(); }
    setMenu({label: request.kind === 'message' ? '消息操作' : '会话操作', point: {x: event.clientX + 4, y: event.clientY + 4}, trigger, items: transcriptMenuItems(entries, run)});
  };
  const closeMenu = useCallback(() => { menuPinned.current = null; applyPins(); setMenu(null); }, [applyPins]);
  const attachmentsAdapter = useMemo<AttachmentAdapter>(() => ({
    accept: '*',
    add: async ({file}) => { const a = await importFile(store.rpc, file); return {...attachmentDescriptor(a), file, status: {type: 'requires-action', reason: 'composer-send'}}; },
    remove: async () => { /* Contract has no deletion RPC: detach only; storage remains host-owned. */ },
    send: async a => ({...a, file: undefined, content: [], status: {type: 'complete'}}),
  }), [store]);
  const runtime = useExternalStoreRuntime<Message>({
    // The runtime owns the full loaded history; the virtual list only decides which rows mount, so a
    // full-history index keeps pointing at the same message across expansion and recycling.
    messages, convertMessage,
    isRunning: Boolean(store.running(session.id)), isLoading: Boolean(state.loading),
    isDisabled: readOnly(session)||Boolean(session.archived), isSendDisabled: !state.connected || Boolean(state.submitting) || unknownAuto,
    adapters: {attachments: attachmentsAdapter},
    onNew: async message => {
      const text = message.content.filter(p => p.type === 'text').map(p => p.text).join('');
      const attachments = message.attachments || [];
      try {
        // 每条输入都走普通提交：没有快捷续接分支，追问同一句也由普通任务重新捕获目标。
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
  const appendWelcomePrompt = (label: string) => {
    const prompt = label === '获得纠音建议' ? '与标准音对比，指出发音不足之处与待改进点' : label;
    const draft = runtime.thread.composer.getState().text;
    runtime.thread.composer.setText(draft ? `${draft}\n\n${prompt}` : prompt);
  };
  useEffect(() => {
    const initialAttachments = savedAttachments.current;
    runtime.thread.composer.setText(store.getView(session.id).draft ?? session.draft ?? '');
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
  // Two-phase hydrate: paint the tail of the history first, then widen to the steady logical window.
  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => setHydrated(true));
    return () => cancelAnimationFrame(frame);
  }, [session.id]);
  // The requirement has to outlive the restore: once `restored` flips, an uncommitted
  // window would shrink back to the steady minimum and drop the anchored row.
  useEffect(() => { if (required) setWindowSize(current => Math.max(current, required)); }, [required]);
  // Restore once, through the unified positioning entry. The engine steers to the target by
  // estimate then refines it after the row measures, so this fires the request and lets the
  // per-frame batch finish it; `restored` only gates when the reader's own scrolls may write.
  useLayoutEffect(() => {
    if (state.loading || restored || !hydrated) return;
    const handle = transcript.current;
    if (handle) {
      const anchor = initialAnchor.current;
      if (anchor && messages.some(message => message.id === anchor.messageId)) handle.position(anchor.messageId, anchor.offset);
      // `anchorTo:'end'` only re-anchors as rows change; on attach the virtualizer still starts
      // at the top. A session with no durable position opens on the latest reply, like a chat.
      else if (initialScroll.current > 0) handle.scrollToOffset(initialScroll.current);
      else handle.scrollToLatest();
    }
    const frame = requestAnimationFrame(() => setRestored(true));
    return () => cancelAnimationFrame(frame);
  }, [hydrated, restored, state.loading, messages]);
  return <Context.Provider value={{store, smooth: Boolean(boot.settings.preferences.smooth_stream), liveTasks, seen: seenMessages.current, disclosures: disclosures.current, holdDisclosure, menu: openMenu}}><AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root className="thread" style={{'--conversation-width': `${conversation.width}px`} as CSSProperties}><header className="chat-header" onPointerDown={event => { if (event.button !== 0) return; const target = event.target as HTMLElement; if (target.closest('button, a, input, select, textarea, [contenteditable]')) return; void store.rpc('window.drag', {}).catch(() => {}); }}><div><h1><Folder size={14} aria-hidden="true"/><span className="chat-title-text" title={session.title || '新会话'}>{session.title || '新会话'}</span>{readOnly(session) && <span className="badge">旧记录 · 只读</span>}</h1></div><div className="actions"><IconButton label={searchOpen ? '关闭会话内查找' : '在会话中查找（Ctrl / Cmd + K）'} aria-pressed={searchOpen} onClick={() => setSearchOpen(open => !open)}><Search size={17}/></IconButton><IconButton label="刷新当前会话" onClick={() => void store.load(session.id).catch(store.report)}><RefreshCw size={17}/></IconButton></div></header>{session.archived && <div className="archived-conversation"><span>此会话已归档</span><button disabled={state.organizationBusy} onClick={() => void store.archive(session.id,false).catch(store.report)}>恢复会话</button></div>}{session.forkedFrom && <div className="fork-origin">分叉自 {session.forkedFromTitle || session.forkedFrom}</div>}{searchOpen && <ChatSearch getContainer={transcriptElement} getViewport={viewportElement} messages={messages} logicalStart={logicalStart} onLocate={locateMessage} onClose={() => setSearchOpen(false)}/>}{unknownAuto && <div className="capability-banner"><CircleAlert size={16}/><span>请配置模型的上下文窗口</span><button onClick={onSettings}>配置真实窗口 / 手动预算</button></div>}<div className="transcript-shell" onContextMenu={event => { if (!event.defaultPrevented) openMenu(event, {kind: 'conversation'}); }}><ThreadPrimitive.Viewport ref={viewport} className="transcript" tabIndex={0} autoScroll={false} scrollToBottomOnInitialize={false} scrollToBottomOnThreadSwitch={false} scrollToBottomOnRunStart={false} aria-label="会话消息">
    {windowState.hiddenAbove > 0 && <div className="history-reveal"><button type="button" onClick={revealEarlier}>显示更早的 {windowState.hiddenAbove} 条消息</button></div>}<div className="transcript-content"><VirtualTranscript ref={transcript} viewportRef={viewport} messages={messages} logicalStart={logicalStart} onAnchor={handleAnchor} onFlush={flushView} onReachTop={revealEarlier} onRevealTo={revealToIndex} onFollowChange={handleFollowChange} pinnedIds={pinned}>{rows => rows.map(row => {
      const message = messages[row.fullIndex];
      if (!message) return null;
      return <div key={row.key} role="listitem" aria-posinset={row.fullIndex + 1} aria-setsize={messages.length} className="transcript-row" data-index={row.index} style={{position: 'absolute', top: row.top, left: 0, right: 0}} ref={row.measure}><TranscriptMessage index={row.fullIndex} messageId={row.key}/></div>;
    })}</VirtualTranscript></div>
  </ThreadPrimitive.Viewport><ThreadPrimitive.Empty><div className="welcome"><div className="welcome-mark"><img src={praatIcon} alt=""/></div><h2>我们应该做些什么</h2><p>调动原生Praat工具测量计算或使用LLM获得纠音建议</p><div className="welcome-cards">{['分析音高与时长','计算VOT','获得纠音建议'].map(text => <button key={text} onClick={() => appendWelcomePrompt(text)}>{text}</button>)}</div></div></ThreadPrimitive.Empty><ConversationMinimap markers={markers} getRow={rowAtOffset} subscribe={subscribeViewport} viewport={viewport} hiddenAbove={windowState.hiddenAbove} indexOffset={logicalStart} onRevealEarlier={revealEarlier} onJump={(messageId, offset, behavior) => transcript.current?.position(messageId, offset, {behavior})}/><ConversationWidthHandles viewport={viewport} width={conversation.width} onPreview={conversation.preview} onCommit={conversation.commit}/></div><div className="jump-wrap">{!following && <button type="button" className="jump-button" aria-label="回到最新消息" onClick={() => transcript.current?.scrollToLatest()}>回到最新<ArrowDown size={16}/></button>}</div><Composer store={store} session={session} composer={runtime.thread.composer} onSettings={onSettings}/><ContextMenu state={menu} onClose={closeMenu}/></ThreadPrimitive.Root></AssistantRuntimeProvider></Context.Provider>;
}
