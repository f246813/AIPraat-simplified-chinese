import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {ChatStore, shallowEqual, useChatSelector, type State} from '../src/store';
import type {Bootstrap, HostEvent, Rpc, Session, Task} from '../src/types';

const session = (id: string): Session => ({id, title: id, readOnly: false, created: '', updated: '', draft: '', scroll: 0});
const boot: Bootstrap = {sessions: [session('a'), session('b')], tasks: [], providers: [], models: [], settings: {api: {}, local: {}, preferences: {}, analysis: {}, alignment: {}}, host: {name: 'fixture', version: '', cloudAllowed: false}};

function storeWithPoll(getPoll: () => {events: HostEvent[]; cursor: number}) {
  const rpc = vi.fn(async (method: string) => {
    if (method === 'bootstrap') return structuredClone(boot);
    if (method === 'sessions.get') return {session: session('a'), messages: []};
    if (method === 'events.poll') return getPoll();
    return {ok: true};
  });
  return {rpc: rpc as unknown as Rpc & ReturnType<typeof vi.fn>, store: new ChatStore(rpc as unknown as Rpc)};
}

afterEach(() => {cleanup(); vi.restoreAllMocks();});

describe('no-change notification suppression', () => {
  it('an empty batch while connected notifies zero times but still advances the cursor', async () => {
    let cursor = 0; let events: HostEvent[] = [];
    const {store} = storeWithPoll(() => ({events, cursor}));
    await store.initialize(); // sets connected
    let notified = 0;
    const unsubscribe = store.subscribe(() => {notified++;});
    cursor = 7;
    await store.pollOnce();
    expect(notified).toBe(0);
    // The cursor advanced: a stale, pre-cursor event that later reappears is ignored.
    events = [{seq: 3, sessionId: 'a', taskId: 't', type: 'delta', payload: {messageId: 'm', text: 'stale'}}];
    cursor = 8;
    await store.pollOnce();
    expect(notified).toBe(0);
    // A genuinely new event past the cursor does notify.
    events = [{seq: 9, sessionId: 'a', taskId: 't', type: 'delta', payload: {messageId: 'm', text: 'live'}}];
    cursor = 9;
    await store.pollOnce();
    expect(notified).toBeGreaterThan(0);
    expect(store.getSnapshot().messages.a[0].content).toBe('live');
    unsubscribe(); store.stop();
  });

  it('a task terminal event on an unrelated field is still a real change that notifies', async () => {
    let poll: {events: HostEvent[]; cursor: number} = {events: [], cursor: 0};
    const {store} = storeWithPoll(() => poll);
    await store.initialize();
    let notified = 0;
    const unsubscribe = store.subscribe(() => {notified++;});
    const task: Task = {id: 't', sessionId: 'a', status: 'running', model: 'm', created: ''};
    poll = {events: [{seq: 1, sessionId: 'a', taskId: 't', type: 'task', payload: task}], cursor: 1};
    await store.pollOnce();
    expect(notified).toBeGreaterThan(0);
    unsubscribe(); store.stop();
  });
});

describe('narrow selectors via useSyncExternalStoreWithSelector', () => {
  it('a background session delta does not re-render the sessions or other-session projections', async () => {
    let poll: {events: HostEvent[]; cursor: number} = {events: [], cursor: 0};
    const {store} = storeWithPoll(() => poll);
    await store.initialize();
    await store.load('b');
    let sessionsRenders = 0; let bRenders = 0; let aRenders = 0;
    renderHook(() => {sessionsRenders++; return useChatSelector(store, (s: State) => s.sessions, shallowEqual);});
    renderHook(() => {bRenders++; return useChatSelector(store, (s: State) => s.messages.b);});
    const {rerender} = renderHook(() => {aRenders++; return useChatSelector(store, (s: State) => s.messages.a);});
    const before = [sessionsRenders, bRenders, aRenders];
    // Stream text only into session 'a'.
    poll = {events: [{seq: 1, sessionId: 'a', taskId: 'ta', type: 'delta', payload: {messageId: 'ma', text: '增量'}}], cursor: 1};
    await act(async () => {await store.pollOnce();});
    expect(store.getSnapshot().messages.a[0].content).toBe('增量');
    // Sessions list identity unchanged → its consumer did not re-render.
    expect(sessionsRenders).toBe(before[0]);
    // Session 'b' messages unchanged → its consumer did not re-render.
    expect(bRenders).toBe(before[1]);
    void rerender;
    store.stop();
  });

  it('shallowEqual compares one level and treats stable references as equal', () => {
    const a = {x: 1, y: {z: 2}};
    expect(shallowEqual(a, {...a})).toBe(true);
    expect(shallowEqual(a, {x: 1, y: {z: 2}})).toBe(false); // nested object identity differs
    expect(shallowEqual({x: 1}, {x: 1, y: undefined})).toBe(false); // key count differs
  });
});

describe('per-session view snapshot hot path', () => {
  it('draft and scroll updates never rebuild or notify on the reactive sessions array', async () => {
    vi.useFakeTimers();
    const {store} = storeWithPoll(() => ({events: [], cursor: 0}));
    await store.initialize();
    const sessionsBefore = store.getSnapshot().sessions;
    let notified = 0;
    const unsubscribe = store.subscribe(() => {notified++;});
    // The hot path: keystroke-scale draft writes and scroll/anchor updates.
    store.view('a', {draft: '打'});
    store.view('a', {draft: '打字'});
    store.view('a', {scroll: 480, anchor: {messageId: 'm', offset: 12}});
    expect(store.getView('a')).toMatchObject({draft: '打字', scroll: 480, anchor: {messageId: 'm', offset: 12}});
    // Nothing reached the host yet and the reactive array identity is untouched.
    expect(store.getSnapshot().sessions).toBe(sessionsBefore);
    expect(notified).toBe(0);
    unsubscribe();
    // The debounced flush persists the accumulated draft/scroll/anchor.
    await vi.advanceTimersByTimeAsync(500);
    store.stop();
  });

  it('a debounced flush sends the accumulated draft and scroll once', async () => {
    vi.useFakeTimers();
    const {rpc, store} = storeWithPoll(() => ({events: [], cursor: 0}));
    await store.initialize();
    store.view('a', {draft: '甲', scroll: 123});
    await vi.advanceTimersByTimeAsync(500);
    const views = rpc.mock.calls.filter(call => call[0] === 'sessions.view').map(call => call[1]);
    expect(views).toEqual([{sessionId: 'a', draft: '甲', scroll: 123}]);
    store.stop();
  });
});
