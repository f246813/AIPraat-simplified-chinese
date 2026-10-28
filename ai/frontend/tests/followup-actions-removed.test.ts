/**
 * 回答结束后的「解释结果／分析原材料／通用讨论」快捷续接整组已删除。
 *
 * 三条边界都要守住：聊天界面不再渲染按钮、桥与类型不再放行 `tasks.followup`、
 * 用户把同一句话打进输入框时仍然只是普通 `tasks.submit`（追问能力本身没变）。
 */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatStore } from '../src/store';
import type { Bootstrap, Rpc, Session } from '../src/types';

// vitest 的 root 是 ai/frontend：源码按工作目录读取，不依赖被打包改写的 import.meta.url。
const source = (relative: string) => readFileSync(join(process.cwd(), 'src', relative), 'utf8');

afterEach(() => vi.useRealTimers());

describe('removed quick follow-up actions', () => {
  it('never renders quick follow-up buttons or keeps a continuation draft', () => {
    const chat = source('Chat.tsx');
    expect(chat).not.toMatch(/followup_actions|tasks\.followup|draftFollowup|pendingFollowup/);
    expect(chat).not.toMatch(/解释结果|分析原材料|通用讨论/);
    // 普通提交仍在：每条输入都是 tasks.submit。
    expect(chat).toContain('await store.submit(session.id, text, attachments.map(a => a.id));');
  });

  it('drops the quick continuation from the contract and the renderer allowlist', () => {
    expect(source('types.ts')).not.toMatch(/followup_actions|tasks\.followup|sourceTaskId/);
    expect(source('bridge.ts')).not.toMatch(/tasks\.followup/);
    expect(existsSync(join(process.cwd(), 'src', 'composer-send.ts'))).toBe(false);
  });

  it('sends the same follow-up wording as an ordinary task, never a continuation RPC', async () => {
    const session = (id: string): Session => ({id, title: id, readOnly: false, created: '', updated: '', draft: '', scroll: 0});
    const boot: Bootstrap = {sessions: [session('a')], tasks: [], settings: {api: {}, local: {}, preferences: {}, analysis: {}, alignment: {}},
      host: {name: 'test', version: '1', cloudAllowed: false}, models: [], providers: []};
    const rpc = vi.fn(async (method: string, params: any) => {
      if (method === 'bootstrap') return structuredClone(boot);
      if (method === 'sessions.get') return {session: session(params.sessionId), messages: []};
      if (method === 'events.poll') return {events: [], cursor: 0};
      if (method === 'tasks.submit') return {id: 't1', sessionId: params.sessionId, status: 'running', model: 'm', created: ''};
      return {ok: true};
    });
    const store = new ChatStore(rpc as Rpc);
    await store.initialize();
    try {
      await store.submit('a', '本次解释结果', []);
      expect(rpc.mock.calls.filter(([method]) => method === 'tasks.submit').map(([, params]) => params))
        .toEqual([{sessionId: 'a', text: '本次解释结果', attachmentIds: []}]);
      expect(rpc.mock.calls.some(([method]) => String(method).startsWith('tasks.') && method !== 'tasks.submit')).toBe(false);
    } finally { store.stop(); }
  });
});
