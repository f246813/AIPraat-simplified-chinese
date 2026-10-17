import {expect, it, vi} from 'vitest';
import {ChatStore} from '../src/store';
import {createDemoAdapter} from '../src/demo';
import type {Rpc, Session} from '../src/types';

it('opens a blank new session without loading pinned or legacy history', async () => {
  const demo=createDemoAdapter();
  await demo('sessions.pin',{sessionId:'demo-history',pinned:true});
  await demo('sessions.view',{sessionId:'demo-history',draft:'历史草稿',scroll:142});
  const before=await demo('bootstrap',{});
  const rpc=vi.fn(demo);
  const store=new ChatStore(rpc as Rpc);
  await store.initialize({newSession:true});
  const state=store.getSnapshot();
  const selected=state.sessions.find(s=>s.id===state.selected)!;
  expect(selected.title).toBe('新会话');
  expect(before.sessions.some(s=>s.id===selected.id)).toBe(false);
  expect(selected.draft).toBe('');
  expect(selected.scroll).toBe(0);
  expect(state.messages[selected.id]).toEqual([]);
  expect(rpc.mock.calls.filter(([method])=>method==='sessions.get').map(([,params])=>(params as {sessionId:string}).sessionId)).toEqual([selected.id]);
  const after=await demo('bootstrap',{});
  for(const history of before.sessions) expect(after.sessions.find(s=>s.id===history.id)).toEqual(history);
  store.stop();
});

it('refreshes and event resets keep the selection; reopening creates a different session', async () => {
  const demo=createDemoAdapter();
  let reset=true;
  const rpc=vi.fn(async(method:string,params:any)=>{
    if(method==='events.poll'){
      const response={events:[],cursor:40,reset};reset=false;return response;
    }
    return demo(method as any,params);
  }) as unknown as Rpc;
  const store=new ChatStore(rpc);
  await store.initialize({newSession:true});
  const first=store.getSnapshot().selected;
  await store.pollOnce();
  expect(store.getSnapshot().selected).toBe(first);
  await store.select('demo-history');
  await store.initialize();
  expect(store.getSnapshot().selected).toBe('demo-history');
  expect(store.getSnapshot().messages['demo-history'].length).toBeGreaterThan(0);
  expect(vi.mocked(rpc).mock.calls.filter(([method])=>method==='sessions.create')).toHaveLength(1);
  store.stop();
  const reopened=new ChatStore(rpc);
  await reopened.initialize({newSession:true});
  expect(reopened.getSnapshot().selected).not.toBe(first);
  expect(reopened.getSnapshot().sessions.some(s=>s.id===first)).toBe(true);
  expect(reopened.getSnapshot().messages[reopened.getSnapshot().selected!]).toEqual([]);
  reopened.stop();
});

it('creates one usable session when opening an empty workspace', async () => {
  const boot=await createDemoAdapter()('bootstrap',{});
  const sessions:Session[]=[];
  const rpc=vi.fn(async(method:string)=>{
    if(method==='bootstrap')return {...boot,sessions:[...sessions]};
    if(method==='sessions.create'){
      const created:Session={id:'first',title:'新会话',created:'',updated:'',draft:'',scroll:0,readOnly:false};
      sessions.push(created);return created;
    }
    if(method==='sessions.get')return {session:sessions[0],messages:[]};
    throw new Error('unexpected '+method);
  });
  const store=new ChatStore(rpc as Rpc);
  await store.initialize({newSession:true});
  expect(store.getSnapshot().selected).toBe('first');
  expect(store.getSnapshot().sessions).toHaveLength(1);
  expect(rpc.mock.calls.filter(([method])=>method==='sessions.create')).toHaveLength(1);
  store.stop();
});
it('defers a new session to local memory when 不落库 is selected', async () => {
  const demo=createDemoAdapter();
  const rpc=vi.fn(async(method:string,params:any)=>{
    const result=await demo(method as any,params);
    if(method==='bootstrap')return {...result,settings:{...result.settings,preferences:{...result.settings.preferences,save_empty_sessions:false,empty_session_policy:'never_persist'}}};
    return result;
  });
  const store=new ChatStore(rpc as Rpc);
  await store.initialize({newSession:true});
  let state=store.getSnapshot();
  const draft=state.sessions.find(s=>s.id===state.selected)!;
  expect(store.isLocalDraft(draft.id)).toBe(true);
  expect(state.messages[draft.id]).toEqual([]);
  // Nothing is written until the first message.
  expect(rpc.mock.calls.filter(([method])=>method==='sessions.create')).toHaveLength(0);
  await store.submit(draft.id,'你好',[]);
  state=store.getSnapshot();
  expect(store.isLocalDraft(state.selected!)).toBe(false);
  expect(rpc.mock.calls.filter(([method])=>method==='sessions.create')).toHaveLength(1);
  store.stop();
});

it('saves empty sessions immediately when 保存空会话 is on', async () => {
  const demo=createDemoAdapter();
  const rpc=vi.fn(async(method:string,params:any)=>{
    const result=await demo(method as any,params);
    if(method==='bootstrap')return {...result,settings:{...result.settings,preferences:{...result.settings.preferences,save_empty_sessions:true,empty_session_policy:'never_persist'}}};
    return result;
  });
  const store=new ChatStore(rpc as Rpc);
  await store.initialize({newSession:true});
  expect(store.isLocalDraft(store.getSnapshot().selected!)).toBe(false);
  expect(rpc.mock.calls.filter(([method])=>method==='sessions.create')).toHaveLength(1);
  store.stop();
});
