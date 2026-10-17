export type Config = Record<string, unknown>;
import type { ThreadSection } from './codex/protocol/ThreadSection';
export type HistorySection = ThreadSection & {builtin?: boolean};
export interface OrganizationSnapshot {sessions: Session[]; sections: HistorySection[]}
export interface WorkflowPolicy extends Config {schema_version:1; preset:'fast'|'balanced'|'deep'|'chat'|'custom'; l0_enabled:boolean; l1_repair_limit:0|1; l2_mode:'off'|'on_demand'|'always'; l2_detail:'brief'|'full'; l2_deadline_sec:number; l2_request_limit:1|2; l3_mode:'off'|'manual'|'auto'; l4_enabled:boolean}
export interface ResultFact {id:string; tool:string; arguments:Config; rows:string[]; status:string; execution:string; source:string; evidence_ref:number|null; result_valid?:boolean; object?:{id:number;class_name:string;name:string}|null; quantities?:{name:string;value:number;unit:string}[]; range:Config; reason:string}
export interface Settings { workflow?: WorkflowPolicy; server?: Config; api: Config; local: Config; preferences: Config; analysis: Config; alignment: Config; [key: string]: unknown }
/** The durable reading position: a message id plus the viewport offset of its top edge. */
export interface ReadingAnchor { messageId: string; offset: number }
export interface Session { id: string; title: string; created: string; updated: string; readOnly: boolean; pinned?: boolean; draft: string; scroll: number; anchor?: ReadingAnchor | null; sectionId?: string | null; sectionPosition?: number | null; archived?: boolean; forkedFrom?: string | null; forkedFromTitle?: string | null }
export interface Session { projectPath?: string|null; connectionId?: string; connectionLabel?: string }
export interface Session { timeGroupDetached?: boolean }
export type HistoryGroupAction = 'delete' | 'detach' | 'archive';
export interface RetainedTimeGroup {mode:'list'|'project'|'connection'; origin:{id:string;label:string;detail:string}|null; group:'today'|'yesterday'|'thisWeek'|'older14d'|'archived'}
export interface Attachment { id: string; name: string; mime: string; size: number }
export interface Activity { id: string; type: 'progress' | 'thinking' | 'tool' | 'compression'; text?: string; name?: string; args?: object; result?: unknown; status?: string; execution?: string }
export interface Message { results?:ResultFact[]; result_content?:string; analysis_content?:string; followup_actions?:('L2'|'L3'|'L4')[]; id: string; role: 'user' | 'assistant'; content: string; status?: 'running' | 'complete' | 'partial' | 'cancelled' | 'failed' | 'interrupted'; taskId?: string; attachments?: Attachment[]; activities?: Activity[] }
export interface Task { workflow?:WorkflowPolicy; id: string; sessionId: string; status: string; model: string; created: string }
export interface HostEvent { seq: number; sessionId: string; taskId: string; type: 'task' | 'delta' | 'activity' | 'message'; payload: unknown }
export interface PraatSnapshot { pid: number; objects: {id: number; className: string; name: string; selected: boolean}[] }
export interface Bootstrap { sessions: Session[]; sections?: HistorySection[]; settings: Settings; tasks: Task[]; host: {name: string; version: string; cloudAllowed: boolean; praat?: PraatSnapshot | null}; providers: {label: string; base_url: string; model: string; hint: string}[]; models: {id: string; contextWindow: number; maxTokens: number}[] }
export interface TestResult { status: string; reason: string; details?: unknown; identity?: unknown }
export interface Preview { name: string; mime: string; text?: string; dataUrl?: string }
export interface ContextStatus { sessionId: string; model: string; estimated: true; inputTokens: number; overheadTokens: number; contextWindow: number | null; reservedTokens: number | null; availableTokens: number | null; percent: number | null; tokenMode: string; windowSource: 'metadata' | 'configured' | 'unknown'; reason: string; exclusions: string[] }
export interface Methods {
  bootstrap: [{}, Bootstrap];
  'sessions.create': [{title?: string; sectionId?: string | null}, Session];
  'sessions.fork': [{sessionId: string}, Session];
  'sessions.section': [{sessionId: string; sectionId: string | null; beforeSessionId?: string}, OrganizationSnapshot];
  'sessions.archive': [{sessionId: string; archived: boolean}, OrganizationSnapshot];
  'sessions.group': [{sessionIds: string[]; action: HistoryGroupAction}, OrganizationSnapshot];
  'sections.create': [{name: string; appearance?: HistorySection['appearance']}, HistorySection];
  'sections.update': [{sectionId: string; name: string; appearance?: HistorySection['appearance']}, HistorySection];
  'sections.delete': [{sectionId: string}, OrganizationSnapshot];
  'sections.archive': [{sectionId: string | null; archived: boolean}, OrganizationSnapshot];
  'sessions.get': [{sessionId: string}, {session: Session; messages: Message[]; cursor?: number}];
  'sessions.search': [{searchTerm: string; archived?: boolean}, {sessionIds: string[]}];
  'sessions.context': [{sessionId: string; text: string; attachmentIds?: string[]}, ContextStatus];
  'window.drag': [{}, {ok: boolean; reason?: string}];
  'sessions.rename': [{sessionId: string; title: string}, {ok: true}];
  'sessions.pin': [{sessionId: string; pinned: boolean}, Session];
  'sessions.delete': [{sessionId: string}, {ok: true}];
  'sessions.view': [{sessionId: string; draft?: string; scroll?: number; anchor?: ReadingAnchor}, {ok: true}];
  'tasks.submit': [{sessionId: string; text: string; attachmentIds?: string[]}, Task];
  'tasks.followup': [{sessionId:string; sourceTaskId:string; mode:'L2'|'L3'|'L4'}, Task];
  'tasks.cancel': [{taskId: string}, {ok: true}];
  'events.poll': [{after: number}, {events: HostEvent[]; cursor: number; reset?: boolean; navigation?: {id: string; category: '模型'}}];
  'settings.get': [{}, Settings];
  'settings.save': [{settings: Partial<Settings>}, Settings];
  'settings.test': [{kind: 'text' | 'audio'; settings: Settings}, TestResult];
  'settings.choose_model': [{}, {path: string|null}];
  'attachments.choose': [{}, Attachment[]];
  'attachments.import': [{name: string; mime: string; data: string}, Attachment];
  'attachments.preview': [{attachmentId: string}, Preview];
  'links.open': [{url: string}, {ok: true}];
}
export type Rpc = <M extends keyof Methods>(method: M, params: Methods[M][0]) => Promise<Methods[M][1]>;
export const readOnly = (session: Session) => session.readOnly || session.id.startsWith('legacy:');
export const activeTask = (task: Task) => ['queued', 'running', 'cancelling', 'pending', 'submitted'].includes(task.status);
export function knownModel(models: Bootstrap['models'], config: Config) {
  try {
    if (new URL(String(config.base_url || '')).hostname.toLowerCase() !== 'api.openai.com') return undefined;
  } catch { return undefined; }
  return models.find(m => m.id === config.model);
}
