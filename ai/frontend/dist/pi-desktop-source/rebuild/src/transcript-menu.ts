/**
 * The transcript's context-menu vocabulary as pure functions of the row's state,
 * so the item set for a state is a value a test can assert and the components
 * stay wiring.
 *
 * Vocabulary ported from PI-Desktop `features/chat/transcript/menu-items.tsx`
 * (fixed revision `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0), reduced
 * to what this host can honestly do:
 * - dropped `edit` / `delete` / revisions: the capture contract has no
 *   message-mutation RPC, and inventing one would contradict the persisted
 *   evidence record.
 * - dropped `regenerate` / `branch`: `tasks.submit` starts a new task from a new
 *   target; it cannot re-run a settled turn.
 * - added `copy-evidence`: this project's core object is the structured tool
 *   result, which already sits in the row.
 */
import type { Activity } from './types';

export type TranscriptMenuAction =
  | 'copy-message' | 'select-message' | 'copy-evidence'
  | 'copy-conversation' | 'select-conversation' | 'scroll-top' | 'scroll-bottom';

export type TranscriptMenuEntry = {id: TranscriptMenuAction; label: string; disabled?: boolean; separatorBefore?: boolean};

export type EvidenceEntry = {name: string; status?: string; execution?: string; args?: unknown; result: unknown};

/** Only delivered tool results are evidence; progress and thinking rows carry no measurement. */
export function collectEvidence(activities: Activity[] | undefined): EvidenceEntry[] {
  return (activities || []).filter(activity => activity.type === 'tool' && activity.result !== undefined).map(activity => ({
    name: activity.name || '工具执行',
    ...(activity.status ? {status: activity.status} : {}),
    ...(activity.execution ? {execution: activity.execution} : {}),
    ...(activity.args !== undefined ? {args: activity.args} : {}),
    result: activity.result,
  }));
}

export const evidencePayload = (entries: EvidenceEntry[]) => JSON.stringify(entries.length === 1 ? entries[0] : entries, null, 2);

export function messageMenuEntries({text, hasEvidence}: {text: string; hasEvidence: boolean}): TranscriptMenuEntry[] {
  const entries: TranscriptMenuEntry[] = [];
  if (text.trim()) {
    entries.push({id: 'copy-message', label: '复制消息'});
    entries.push({id: 'select-message', label: '选择消息文本'});
  }
  if (hasEvidence) entries.push({id: 'copy-evidence', label: '复制结构化证据', separatorBefore: entries.length > 0});
  return entries;
}

export function conversationMenuEntries({hasMessages}: {hasMessages: boolean}): TranscriptMenuEntry[] {
  return [
    // Full-session copy is a data-path operation; the selection item only ever covers the rows the
    // virtual list currently shows, so its label says which of the two the reader is getting.
    {id: 'copy-conversation', label: '复制整个会话', disabled: !hasMessages},
    {id: 'select-conversation', label: '选择当前显示内容', separatorBefore: true},
    {id: 'scroll-top', label: '跳到顶部', separatorBefore: true},
    {id: 'scroll-bottom', label: '回到最新'},
  ];
}

/** A live selection inside the row wins over the whole message, as PI-Desktop's copy does. */
export function copyPayload(selection: string | undefined, fallback: string): string {
  return (selection || '').trim() || fallback.trim();
}

export const transcriptText = (messages: {role: string; content: string}[]): string =>
  messages.filter(message => message.content.trim()).map(message => `${message.role === 'user' ? '你' : '声学助手'}：\n${message.content.trim()}`).join('\n\n');
