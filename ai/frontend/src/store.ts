import { useSyncExternalStore } from 'react';
import { activeTask, readOnly, type Attachment, type Bootstrap, type HistorySection, type HistoryGroupAction, type OrganizationSnapshot, type HostEvent, type Message, type ReadingAnchor, type Rpc, type Session, type Settings, type Task } from './types';
export interface State { boot?: Bootstrap; sessions: Session[]; messages: Record<string, Message[]>; tasks: Record<string, Task>; selected?: string; loading: Record<string, boolean>; submitting: Record<string, boolean>; error?: string; connected: boolean; organizationBusy?: boolean; modelSettingsRequest?: string }
const organizationMetadata=(session:Session):Partial<Session>=>Object.fromEntries((['pinned','sectionId','sectionPosition','archived','timeGroupDetached','forkedFrom','forkedFromTitle'] as const).filter(key=>key in session).map(key=>[key,session[key]]));
export function applyEvent(state: State, event: HostEvent): State {
  const {sessionId, taskId, payload} = event;
  // Never infer attribution from the selected UI thread.
  const existing = state.tasks[taskId];
  if (existing && existing.sessionId !== sessionId) return state;
  if (event.type === 'task') {
    const task = payload as Task;
    if (task.id !== taskId || task.sessionId !== sessionId) return state;
    return {...state, tasks: {...state.tasks, [task.id]: task}};
  }
  const messages = [...(state.messages[sessionId] || [])];
  const p = payload as {messageId: string; text: string; activity: NonNullable<Message['activities']>[number]};
  const id = event.type === 'message' ? (payload as Message).id : p.messageId;
  let index = messages.findIndex(m => m.id === id);
  if (index !== -1 && messages[index].taskId && messages[index].taskId !== taskId) return state;
  if (event.type === 'message') {
    const message = payload as Message;
    if (message.taskId && message.taskId !== taskId) return state;
    if (index === -1) messages.push({...message, taskId});
    else messages[index] = {...message, taskId};
  } else {
    if (index === -1) { index = messages.length; messages.push({id, role: 'assistant', content: '', taskId, status: 'running'}); }
    const message = messages[index];
    if (event.type === 'delta') messages[index] = {...message, content: message.content + p.text};
    else if (event.type === 'activity') {
      const activities = [...(message.activities || [])];
      const ai = activities.findIndex(a => a.id === p.activity.id);
      if (ai === -1) activities.push(p.activity); else activities[ai] = p.activity;
      messages[index] = {...message, activities};
    }
  }
  return {...state, messages: {...state.messages, [sessionId]: messages}};
}
export const sortSessions = (sessions: Session[]) => [...sessions].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || Number(readOnly(a)) - Number(readOnly(b)) || b.updated.localeCompare(a.updated));
export class ChatStore {
  private state: State = {sessions: [], messages: {}, tasks: {}, loading: {}, submitting: {}, connected: false};
  private listeners = new Set<() => void>();
  private cursor = 0;
  private journal: HostEvent[] = [];
  private fetching = new Map<string, Promise<void>>();
  private snapshotStreams = new Set<string>();
  private watermarks = new Map<string, number>();
  private pinRevisions = new Map<string, number>();
  private renameRevisions = new Map<string, number>();
  private organizationRevision=0;
  private organizationQueue:Promise<unknown>=Promise.resolve();
  private stopped = true;
  private timer?: ReturnType<typeof setTimeout>;
  private views = new Map<string, {draft?: string; scroll?: number; anchor?: ReadingAnchor}>();
  private viewTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private viewChains = new Map<string, Promise<void>>();
  private drafts = new Set<string>();
  private draftSeq = 0;
  private emptyPolicy: 'delete_on_exit' | 'never_persist' = 'delete_on_exit';
  private saveEmptySessions = false;
  readonly attachments = new Map<string, Attachment[]>(); // transient metadata only; bytes stay in host
  constructor(readonly rpc: Rpc) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(next: State) { this.state = next; this.listeners.forEach(l => l()); }
  report = (error: unknown) => this.update({...this.state, error: error instanceof Error ? error.message : String(error)});
  clearError = () => this.update({...this.state, error: undefined});
  async initialize({newSession = false} = {}) {
    const revision=this.organizationRevision;
    const revisions = new Map(this.pinRevisions);
    const remote = await this.rpc('bootstrap', {});
    // Retain newer organization state, but still reconcile tasks/settings after
    // an event reset: the cursor has advanced and terminal events cannot replay.
    const boot=revision!==this.organizationRevision?{...remote,sections:this.state.boot?.sections||remote.sections,sessions:this.state.sessions}:remote;
    const local = new Map(this.state.sessions.map(s => [s.id, s]));
    // Local unsent drafts never come back from the host bootstrap; keep them.
    const drafts = this.state.sessions.filter(s => this.isLocalDraft(s.id)).map(s => ({...s, ...this.views.get(s.id)}));
    this.update({...this.state, boot, connected: true, sessions: sortSessions([...drafts, ...boot.sessions.map(s => ({...s, ...this.views.get(s.id), ...(local.has(s.id) ? {draft: local.get(s.id)!.draft, scroll: local.get(s.id)!.scroll} : {}), ...(revisions.get(s.id) !== this.pinRevisions.get(s.id) ? {pinned: local.get(s.id)?.pinned} : {})}))]), tasks: Object.fromEntries(boot.tasks.map(t => [t.id, t]))});
    this.syncEmptySessionPolicy();
    if (newSession) {
      await this.create();
      return;
    }
    const selected = this.state.selected;
    if (!selected || (!this.isLocalDraft(selected) && !boot.sessions.some(s => s.id === selected))) {
      this.update({...this.state, selected: boot.sessions.find(s=>!s.archived)?.id});
    }
    if (this.state.selected && !this.isLocalDraft(this.state.selected)) await this.load(this.state.selected);
  }
  async load(id: string) {
    if (this.isLocalDraft(id)) return; // local placeholders have no host row yet
    if (this.fetching.has(id)) return this.fetching.get(id);
    const start = this.cursor;
    const pinRevision = this.pinRevisions.get(id);
    const renameRevision=this.renameRevisions.get(id);
    const organizationRevision=this.organizationRevision;
    this.update({...this.state, loading: {...this.state.loading, [id]: true}});
    const job = (async () => {
      try {
        const result = await this.rpc('sessions.get', {sessionId: id});
        const local = this.state.sessions.find(s => s.id === id);
        const watermark = result.cursor;
        if (watermark !== undefined) this.watermarks.set(id, watermark);
        else if (this.running(id) || result.messages.some(m => m.status === 'running')) this.snapshotStreams.add(id);
        let next = {...this.state, sessions: sortSessions(this.state.sessions.map(s => s.id === id ? {...result.session, ...(organizationRevision!==this.organizationRevision?organizationMetadata(s):{}), ...(pinRevision !== this.pinRevisions.get(id) ? {pinned: s.pinned} : {}), ...(renameRevision!==this.renameRevisions.get(id)?{title:s.title}:{}), draft: local?.draft ?? result.session.draft, scroll: local?.scroll ?? result.session.scroll, anchor: local?.anchor ?? result.session.anchor ?? null} : s)), messages: {...this.state.messages, [id]: result.messages}};
        // Production snapshots and their event watermark are captured atomically.
        // Only replay later events; old message creation must not erase live text.
        // Legacy fixtures without a watermark retain invalidation-based reconciliation.
        for (const e of this.journal) if (e.seq > (watermark ?? start) && e.sessionId === id && (watermark !== undefined || e.type !== 'delta')) next = applyEvent(next, e);
        this.update(next);
      } finally {
        this.fetching.delete(id);
        this.update({...this.state, loading: {...this.state.loading, [id]: false}});
      }
    })();
    this.fetching.set(id, job); return job;
  }
  async select(id: string) {
    const old = this.state.selected;
    if (old) this.flushView(old);
    this.update({...this.state, selected: id});
    await this.load(id);
  }
  isLocalDraft(id: string) { return this.drafts.has(id); }
  /** Codex lazily persists a new conversation only when its first turn starts. */
  private useDrafts() { return this.emptyPolicy === 'never_persist' && !this.saveEmptySessions; }
  private syncEmptySessionPolicy() {
    const prefs = (this.state.boot?.settings?.preferences || {}) as Record<string, unknown>;
    this.emptyPolicy = prefs.empty_session_policy === 'never_persist' ? 'never_persist' : 'delete_on_exit';
    this.saveEmptySessions = Boolean(prefs.save_empty_sessions);
  }
  private startDraft(sectionId?: string|null, force = false): string {
    const blank = (s: Session) => this.isLocalDraft(s.id) && !(s.draft || '').trim() && (this.state.messages[s.id]?.length ?? 0) === 0;
    const current = this.state.sessions.find(s => s.id === this.state.selected);
    const reusable = force ? undefined : current && blank(current) ? current : this.state.sessions.find(blank);
    if (reusable) {
      const patch = sectionId !== undefined && (reusable.sectionId ?? null) !== (sectionId ?? null) ? {sectionId: sectionId ?? null} : {};
      this.update({...this.state, sessions: Object.keys(patch).length ? this.state.sessions.map(s => s.id === reusable.id ? {...s, ...patch} : s) : this.state.sessions, selected: reusable.id});
      return reusable.id;
    }
    const id = `local-draft-${++this.draftSeq}`;
    this.drafts.add(id);
    const now = new Date().toISOString();
    const session: Session = {id, title: '新会话', created: now, updated: now, readOnly: false, draft: '', scroll: 0, anchor: null, ...(sectionId !== undefined ? {sectionId: sectionId ?? null} : {})};
    this.update({...this.state, sessions: sortSessions([session, ...this.state.sessions]), messages: {...this.state.messages, [id]: []}, selected: id});
    return id;
  }
  private async persistNew(sectionId?: string|null) {
    return this.organize(async()=>{
    const session = await this.rpc('sessions.create', sectionId===undefined?{}:{sectionId});
    this.organizationRevision++;
    this.update({...this.state, sessions: sortSessions([session, ...this.state.sessions]), messages: {...this.state.messages, [session.id]: []}});
    await this.select(session.id);
    });
  }
  async create(sectionId?: string|null) {
    if (this.useDrafts()) { this.startDraft(sectionId, true); return; }
    return this.persistNew(sectionId);
  }
  /** Persist a local draft on its first real use and return the host session id. */
  private async persistDraft(id: string): Promise<string> {
    const local = this.state.sessions.find(s => s.id === id);
    const params: {title?: string; sectionId?: string|null} = {};
    if (local?.title && local.title !== '新会话') params.title = local.title;
    if (local && 'sectionId' in local) params.sectionId = local.sectionId ?? null;
    const created = await this.rpc('sessions.create', params);
    this.organizationRevision++;
    this.drafts.delete(id);
    const messages = {...this.state.messages};
    const carried = messages[id]; delete messages[id];
    messages[created.id] = carried || [];
    this.views.delete(id);
    clearTimeout(this.viewTimers.get(id)); this.viewTimers.delete(id);
    const attachments = this.attachments.get(id);
    if (attachments) { this.attachments.delete(id); this.attachments.set(created.id, attachments); }
    const submitting = {...this.state.submitting};
    if (submitting[id]) { delete submitting[id]; submitting[created.id] = true; }
    this.update({...this.state, sessions: sortSessions(this.state.sessions.map(s => s.id === id ? {...created, draft: s.draft, scroll: s.scroll, anchor: s.anchor ?? null} : s)), messages, submitting, selected: this.state.selected === id ? created.id : this.state.selected});
    return created.id;
  }
  private async persistDraftIfNeeded(id: string) { return this.isLocalDraft(id) ? this.persistDraft(id) : id; }
  private removeDraft(id: string) {
    this.drafts.delete(id);
    clearTimeout(this.viewTimers.get(id)); this.viewTimers.delete(id); this.views.delete(id); this.attachments.delete(id);
    const messages = {...this.state.messages}; delete messages[id];
    const sessions = this.state.sessions.filter(s => s.id !== id);
    this.update({...this.state, messages, sessions, selected: this.state.selected === id ? sessions.find(s => !s.archived)?.id : this.state.selected});
  }
  async rename(id: string, title: string) {
    const value = title.trim(); if (!value) throw new Error('请输入会话名称');
    if (this.isLocalDraft(id)) { this.update({...this.state, sessions: this.state.sessions.map(s => s.id === id ? {...s, title: value} : s)}); return; }
    return this.organize(async()=>{
    const session = this.state.sessions.find(s => s.id === id);
    if (!session || readOnly(session)) throw new Error('旧会话只读，不能重命名');
    await this.rpc('sessions.rename', {sessionId: id, title: value});
    this.organizationRevision++;
    this.renameRevisions.set(id,(this.renameRevisions.get(id)||0)+1);
    this.update({...this.state, sessions: this.state.sessions.map(s => s.id === id ? {...s, title: value} : s)});
    });
  }
  async pin(id: string, pinned: boolean) {
    return this.organize(async()=>{
    id = await this.persistDraftIfNeeded(id);
    const current = this.state.sessions.find(s => s.id === id);
    if (!current || readOnly(current)) throw new Error('旧会话只读，不能置顶');
    const result = await this.rpc('sessions.pin', {sessionId: id, pinned});
    this.organizationRevision++;
    this.pinRevisions.set(id, (this.pinRevisions.get(id) || 0) + 1);
    // Authoritative metadata only; never replace local draft, reading position or messages.
    this.update({...this.state, sessions: sortSessions(this.state.sessions.map(s => s.id === id ? {...s, ...organizationMetadata(result), pinned: result.pinned} : s))});
    });
  }
  private organize<T>(operation:()=>Promise<T>):Promise<T> {
    const job=this.organizationQueue.then(async()=>{this.update({...this.state,organizationBusy:true});try{return await operation();}finally{this.update({...this.state,organizationBusy:false});}});
    this.organizationQueue=job.catch(()=>undefined);return job;
  }
  private applyOrganization(snapshot:OrganizationSnapshot) {
    this.organizationRevision++;
    const local=new Map(this.state.sessions.map(s=>[s.id,s]));
    const drafts=this.state.sessions.filter(s=>this.isLocalDraft(s.id)).map(s=>({...s,...this.views.get(s.id)}));
    this.update({...this.state,boot:this.state.boot?{...this.state.boot,sections:snapshot.sections}:undefined,sessions:sortSessions([...drafts,...snapshot.sessions.map(s=>({...s,...(local.has(s.id)?{draft:local.get(s.id)!.draft,scroll:local.get(s.id)!.scroll,anchor:local.get(s.id)!.anchor}:{}),...this.views.get(s.id)}))])});
  }
  async moveToSection(id:string,sectionId:string|null,beforeSessionId?:string) {
    return this.organize(async()=>{ id=await this.persistDraftIfNeeded(id); this.applyOrganization(await this.rpc('sessions.section',{sessionId:id,sectionId,...(beforeSessionId?{beforeSessionId}:{})})); });
  }
  async createSection(name:string,appearance:HistorySection['appearance']=null) {
    return this.organize(async()=>{const section=await this.rpc('sections.create',{name,appearance});this.organizationRevision++;this.update({...this.state,boot:this.state.boot?{...this.state.boot,sections:[...(this.state.boot.sections||[]),section]}:undefined});return section;});
  }
  async updateSection(sectionId:string,name:string,appearance:HistorySection['appearance']) {
    return this.organize(async()=>{const section=await this.rpc('sections.update',{sectionId,name,appearance});this.organizationRevision++;this.update({...this.state,boot:this.state.boot?{...this.state.boot,sections:(this.state.boot.sections||[]).map(s=>s.id===sectionId?section:s)}:undefined});});
  }
  async removeSection(sectionId:string) {
    return this.organize(async()=>this.applyOrganization(await this.rpc('sections.delete',{sectionId})));
  }
  private async selectAfterArchive() {
    if(this.state.sessions.find(s=>s.id===this.state.selected)?.archived){const next=this.state.sessions.find(s=>!s.archived);if(next)await this.select(next.id);else this.update({...this.state,selected:undefined});}
  }
  async archive(id:string,archived:boolean) {
    return this.organize(async()=>{ id=await this.persistDraftIfNeeded(id); this.applyOrganization(await this.rpc('sessions.archive',{sessionId:id,archived}));if(archived)await this.selectAfterArchive();});
  }
  async archiveSection(sectionId:string|null,archived:boolean) {
    return this.organize(async()=>{this.applyOrganization(await this.rpc('sections.archive',{sectionId,archived}));if(archived)await this.selectAfterArchive();});
  }
  async manageGroup(sessionIds:string[],action:HistoryGroupAction) {
    return this.organize(async()=>{
      let realIds=[...sessionIds];
      if(action==='delete') {
        const draftIds=sessionIds.filter(id=>this.isLocalDraft(id));
        for(const id of draftIds) this.removeDraft(id);
        realIds=sessionIds.filter(id=>!draftIds.includes(id));
        if(!realIds.length) return;
      } else {
        realIds=[];
        for(const id of sessionIds) realIds.push(await this.persistDraftIfNeeded(id));
      }
      const ids=new Set(realIds);
      if(action!=='detach'&&realIds.some(id=>this.running(id)||this.state.submitting[id]))throw new Error('分组中有运行会话，不能删除或归档');
      const snapshot=await this.rpc('sessions.group',{sessionIds:realIds,action});
      const selectedDeleted=action==='delete'&&Boolean(this.state.selected&&ids.has(this.state.selected));
      if(action==='delete') {
        const messages={...this.state.messages};
        for(const id of ids){clearTimeout(this.viewTimers.get(id));this.views.delete(id);this.attachments.delete(id);delete messages[id];}
        this.update({...this.state,messages});
      }
      this.applyOrganization(snapshot);
      if(selectedDeleted){const next=this.state.sessions.find(s=>!s.archived);if(next)await this.select(next.id);else this.update({...this.state,selected:undefined});}
      if(action==='archive')await this.selectAfterArchive();
    });
  }
  async fork(id:string) {
    return this.organize(async()=>{ id=await this.persistDraftIfNeeded(id); await this.flushView(id);const session=await this.rpc('sessions.fork',{sessionId:id});this.organizationRevision++;this.update({...this.state,sessions:sortSessions([session,...this.state.sessions])});await this.select(session.id);});
  }
  async delete(id: string) {
    if (this.isLocalDraft(id)) { this.removeDraft(id); return; }
    return this.organize(async()=>{
    const session = this.state.sessions.find(s => s.id === id);
    if (!session || this.running(id) || this.state.submitting[id]) throw new Error('会话不存在或正在运行，不能删除');
    await this.rpc('sessions.delete', {sessionId: id});
    this.organizationRevision++;
    clearTimeout(this.viewTimers.get(id)); this.views.delete(id); this.attachments.delete(id);
    const messages = {...this.state.messages}; delete messages[id];
    const sessions = this.state.sessions.filter(s => s.id !== id);
    const selectedDeleted=this.state.selected===id;
    this.update({...this.state, messages, sessions, selected: selectedDeleted ? sessions.find(s=>!s.archived)?.id : this.state.selected});
    if (selectedDeleted && this.state.selected) await this.load(this.state.selected);
    });
  }
  running(id: string) { return Object.values(this.state.tasks).find(t => t.sessionId === id && activeTask(t)); }
  view(id: string, patch: {draft?: string; scroll?: number; anchor?: ReadingAnchor}) {
    const session = this.state.sessions.find(s => s.id === id);
    if (!session) return;
    if (readOnly(session)) {
      if (patch.scroll !== undefined) this.update({...this.state, sessions: this.state.sessions.map(s => s.id === id ? {...s, scroll: patch.scroll!} : s)});
      return; // Legacy database stays read-only, including viewing metadata.
    }
    this.views.set(id, {...this.views.get(id), ...patch});
    this.update({...this.state, sessions: this.state.sessions.map(s => s.id === id ? {...s, ...patch} : s)});
    if (this.isLocalDraft(id)) return; // local placeholder: keep the draft in memory only
    clearTimeout(this.viewTimers.get(id));
    this.viewTimers.set(id, setTimeout(() => this.flushView(id), 450));
  }
  flushView(id: string) {
    clearTimeout(this.viewTimers.get(id));
    const patch = this.views.get(id); if (!patch) return;
    if (this.isLocalDraft(id)) return; // no host row to persist into yet
    this.views.delete(id);
    const chain = (this.viewChains.get(id) || Promise.resolve()).then(async () => { await this.rpc('sessions.view', {sessionId: id, ...patch}); }).catch(this.report);
    this.viewChains.set(id, chain);
    return chain;
  }
  async submit(id: string, text: string, attachmentIds: string[]) {
    let sessionId = id;
    const session = this.state.sessions.find(s => s.id === sessionId);
    if (!session || readOnly(session)) throw new Error('旧记录只能查看，请新建会话');
    if (session.archived) throw new Error('请先恢复已归档的会话');
    if (this.running(sessionId) || this.state.submitting[sessionId]) throw new Error('本会话任务尚未结束；可以在其他会话继续工作');
    if (!text.trim() && !attachmentIds.length) return;
    this.update({...this.state, submitting: {...this.state.submitting, [sessionId]: true}});
    try {
      // The first message is what creates the session when 不落库 is selected.
      if (this.isLocalDraft(sessionId)) sessionId = await this.persistDraft(sessionId);
      const task = await this.rpc('tasks.submit', {sessionId, text, attachmentIds});
      // A terminal event can arrive before the submit RPC resolves.
      const current = this.state.tasks[task.id];
      this.update({...this.state, tasks: {...this.state.tasks, [task.id]: current || task}});
      this.attachments.delete(sessionId); this.view(sessionId, {draft: ''});
      await this.load(sessionId);
    } catch (e) {
      this.view(sessionId, {draft: text});
      this.report(new Error(`${e instanceof Error ? e.message : String(e)}。未自动重试：请先刷新核对任务及投递状态。`));
      throw e;
    } finally { this.update({...this.state, submitting: {...this.state.submitting, [sessionId]: false, ...(sessionId !== id ? {[id]: false} : {})}}); }
  }
  async cancel(id: string) { const task = this.running(id); if (task) await this.rpc('tasks.cancel', {taskId: task.id}); }
  async save(settings: Partial<Settings>) {
    const result = await this.rpc('settings.save', {settings});
    this.update({...this.state, boot: this.state.boot ? {...this.state.boot, settings: result} : undefined});
    // The host rechecks material availability and model capabilities for visible actions.
    if (this.state.selected) await this.load(this.state.selected).catch(this.report);
    return result;
  }
  start() { if (!this.stopped) return; this.stopped = false; void this.poll(); }
  stop() { this.stopped = true; clearTimeout(this.timer); for (const id of this.views.keys()) this.flushView(id); }
  async pollOnce() {
    const result = await this.rpc('events.poll', {after: this.cursor});
    if (result.navigation?.category === '模型') this.update({...this.state, modelSettingsRequest: result.navigation.id});
    if (result.reset) {
      this.cursor = result.cursor; this.journal = []; this.watermarks.clear(); this.snapshotStreams.clear();
      await this.initialize();
      for (const id of Object.keys(this.state.messages)) if (id !== this.state.selected && this.state.sessions.some(s => s.id === id)) await this.load(id);
      return;
    }
    const finished = new Set<string>(); const dirtyStreams = new Set<string>();
    let state = this.state;
    for (const event of result.events) {
      if (event.seq <= this.cursor) continue;
      if (event.type !== 'task' && event.seq <= (this.watermarks.get(event.sessionId) ?? -1)) continue;
      // Contract v1 snapshots have no cursor watermark. Once a live stream was
      // snapshotted, use delta events as invalidations rather than double-appending
      // bytes already present in that snapshot. Fetch once per polling batch.
      if (event.type === 'delta' && this.snapshotStreams.has(event.sessionId)) dirtyStreams.add(event.sessionId);
      else state = applyEvent(state, event);
      this.journal.push(event);
      if (event.type === 'task' && !activeTask(event.payload as Task)) finished.add(event.sessionId);
    }
    this.cursor = result.cursor;
    this.journal = this.journal.slice(-2000);
    this.update({...state, connected: true});
    for (const id of new Set([...dirtyStreams, ...finished])) if (this.state.messages[id] && this.state.sessions.some(s => s.id === id)) {
      await this.load(id);
      if (finished.has(id) && !this.running(id)) this.snapshotStreams.delete(id);
    }
  }
  private async poll() {
    try { await this.pollOnce(); } catch (e) { this.update({...this.state, connected: false}); this.report(e); }
    if (!this.stopped) this.timer = setTimeout(() => void this.poll(), 220);
  }
}
export const useChatState = (store: ChatStore) => useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
