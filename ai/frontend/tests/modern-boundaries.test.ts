import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatStore } from '../src/store';
import { knownModel, type Rpc } from '../src/types';
afterEach(() => vi.useRealTimers());
describe('modern integration boundaries', () => {
  it('matches model metadata only at the adapted official endpoint', () => {
    const models = [{id:'gpt-4o', contextWindow:128000, maxTokens:16384}];
    expect(knownModel(models, {base_url:'https://api.openai.com/v1', model:'gpt-4o'})).toEqual(models[0]);
    expect(knownModel(models, {base_url:'https://gateway.example/v1', model:'gpt-4o'})).toBeUndefined();
    expect(knownModel(models, {base_url:'http://127.0.0.1/v1', model:'gpt-4o'})).toBeUndefined();
    expect(knownModel(models, {base_url:'bad', model:'gpt-4o'})).toBeUndefined();
  });
  it('keeps legacy reading position transient without sending any writes', async () => {
    vi.useFakeTimers();
    const session = {id:'legacy:old', title:'old', created:'', updated:'', draft:'', scroll:0, readOnly:true};
    const rpc = vi.fn(async (method:string) => method === 'bootstrap' ? {sessions:[session], tasks:[], settings:{}, host:{}, models:[], providers:[]} : {session, messages:[]});
    const store = new ChatStore(rpc as Rpc); await store.initialize();
    store.view(session.id, {scroll:200, draft:'not saved'}); store.flushView(session.id);
    await vi.advanceTimersByTimeAsync(500);
    expect(store.getView(session.id).scroll).toBe(200);
    expect(store.getView(session.id).draft).toBe('');
    expect(store.getSnapshot().sessions[0].scroll).toBe(0);
    expect(rpc.mock.calls.some(([method]) => method === 'sessions.view')).toBe(false);
    store.stop();
  });
});
