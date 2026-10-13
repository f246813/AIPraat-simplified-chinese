import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ThreadComposerRuntime } from '@assistant-ui/react';
import { ComposerStatus, modelOptions, quickModelPatch, quickStrengthPatch, thinkingStrength } from '../src/ComposerStatus';
import { ChatStore } from '../src/store';
import type { Bootstrap, ContextStatus, Rpc, Session } from '../src/types';
afterEach(() => { cleanup(); vi.useRealTimers(); });
const session = (id: string): Session => ({id, title:id, created:'', updated:'', draft:'', scroll:0, readOnly:false});
const boot: Bootstrap = {sessions:[session('a'),session('b')], tasks:[], settings:{api:{enabled:true, model:'api-model', base_url:'https://gateway.example/v1', force_deep_thinking:true}, local:{model:'local-model',base_url:'http://127.0.0.1/v1'}, preferences:{send_key:'enter'}, analysis:{}, alignment:{}}, host:{name:'test', version:'1',cloudAllowed:false},models:[{id:'gpt-4o',contextWindow:128000,maxTokens:16384}],providers:[]};
const status = (id: string, input: number): ContextStatus => ({sessionId:id,model:'api-model',estimated:true,inputTokens:input,overheadTokens:100,contextWindow:10000,reservedTokens:1000,availableTokens:9000-input,percent:input/100,tokenMode:'manual',windowSource:'configured',reason:'',exclusions:['音频编码']});
function composerFixture() {
  const listeners = new Set<() => void>();
  const current = {text:'draft',attachments:[{id:'complete',status:{type:'complete'}},{id:'importing',status:{type:'running'}},{id:'failed',status:{type:'incomplete'}}]};
  return {current, listeners, runtime:{getState:() => current,subscribe:(fn:() => void) => {listeners.add(fn);return () => listeners.delete(fn);}} as unknown as ThreadComposerRuntime};
}
it('quick changes save only the required fields, never credentials/endpoints', () => {
  expect(quickModelPatch('api','next')).toEqual({api:{enabled:true,model:'next'}});
  expect(quickModelPatch('local','next')).toEqual({api:{enabled:false},local:{model:'next'}});
  expect(quickStrengthPatch('api','low')).toEqual({api:{thinking_level:'low',force_deep_thinking:false}});
  expect(quickStrengthPatch('local','off')).toEqual({local:{thinking_level:'off',enable_thinking:false}});
  expect(quickStrengthPatch('local','high')).toEqual({local:{thinking_level:'high',enable_thinking:true}});
  expect(quickStrengthPatch('local','auto')).toEqual({local:{thinking_level:'auto'}});
  expect(thinkingStrength({force_deep_thinking:true,thinking_level:'off'},'api')).toBe('high');
});
it('model suggestions are scoped to the configured official endpoint', () => {
  expect(modelOptions(boot,'api')).toEqual(['api-model']);
  const official = structuredClone(boot); official.settings.api.base_url='https://api.openai.com/v1';
  expect(modelOptions(official,'api')).toEqual(['api-model','gpt-4o']);
  expect(modelOptions(official,'local')).toEqual(['local-model']);
});
it('debounces real host estimates and ignores stale session replies', async () => {
  vi.useFakeTimers(); const pending: ((value: ContextStatus) => void)[] = [];
  const rpc = vi.fn(async (method:string, params:any) => {
    if (method==='bootstrap') return structuredClone(boot);
    if (method==='sessions.get') return {session:session(params.sessionId),messages:[]};
    if (method==='sessions.context') return new Promise<ContextStatus>(resolve => pending.push(resolve));
    throw Error(method);
  });
  const store = new ChatStore(rpc as Rpc); await store.initialize();
  const fixture = composerFixture(); const props={store,composer:fixture.runtime,onSettings:vi.fn()};
  const view=render(<ComposerStatus {...props} session={session('a')}/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(350); });
  expect(rpc.mock.calls.find(([m])=>m==='sessions.context')?.[1]).toEqual({sessionId:'a',text:'draft',attachmentIds:['complete']});
  await act(async () => { pending[0](status('a',1000)); });
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('10%');
  await act(async () => {fixture.current.text='changed-a';fixture.listeners.forEach(fn=>fn());});
  await act(async () => {await vi.advanceTimersByTimeAsync(350);});
  view.rerender(<ComposerStatus {...props} session={session('b')}/>);
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('…');
  await act(async () => { await vi.advanceTimersByTimeAsync(350); pending[2](status('b',2000)); });
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toContain('20%');
  await act(async () => { pending[1](status('a',9000)); });
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toContain('20%');
  expect(screen.getByRole('button',{name:'切换模型与推理强度'}).textContent).toContain('api-model · high');
  view.unmount(); expect(fixture.listeners.size).toBe(0);
});
it('unknown windows and RPC failures never fabricate a percentage', async () => {
  vi.useFakeTimers(); let fail=false;
  const rpc = vi.fn(async (method:string, params:any) => {
    if(method==='bootstrap') return structuredClone(boot);
    if(method==='sessions.get') return {session:session(params.sessionId),messages:[]};
    if(method==='sessions.context') { if(fail) throw Error('host unavailable');return {...status('a',2000),contextWindow:null,reservedTokens:null,availableTokens:null,percent:null,windowSource:'unknown',reason:'窗口未知'}; }
    throw Error(method);
  });
  const store=new ChatStore(rpc as Rpc);await store.initialize();const fixture=composerFixture();
  render(<ComposerStatus store={store} session={session('a')} composer={fixture.runtime} onSettings={vi.fn()}/>);
  await act(async () => {await vi.advanceTimersByTimeAsync(350);});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('—');
  fail=true; await act(async () => {fixture.current.text='new';fixture.listeners.forEach(fn=>fn());});
  await act(async () => {await vi.advanceTimersByTimeAsync(350);});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('—');
  expect(screen.getByRole('alert').textContent).toBe('host unavailable');
});
it('keeps the displayed budget throughout typing, debounce and pending refresh; ignores obsolete replies', async () => {
  vi.useFakeTimers(); const pending: ((value: ContextStatus) => void)[] = [];
  const rpc = vi.fn(async (method:string, params:any) => {
    if (method==='bootstrap') return structuredClone(boot);
    if (method==='sessions.get') return {session:session(params.sessionId),messages:[]};
    if (method==='sessions.context') return new Promise<ContextStatus>(resolve => pending.push(resolve));
    throw Error(method);
  });
  const store=new ChatStore(rpc as Rpc); await store.initialize(); const fixture=composerFixture();
  render(<ComposerStatus store={store} session={session('a')} composer={fixture.runtime} onSettings={vi.fn()}/>);
  await act(async () => {await vi.advanceTimersByTimeAsync(350);pending[0](status('a',2000));});
  const trigger=screen.getByRole('button',{name:'上下文用量'});
  const ring=trigger.querySelector('.ring-value')!;
  const contextCalls=()=>rpc.mock.calls.filter(([method])=>method==='sessions.context');
  for (const text of ['草','草稿','草稿材料']) {
    await act(async () => {fixture.current.text=text;fixture.listeners.forEach(fn=>fn());});
    await act(async () => {await vi.advanceTimersByTimeAsync(100);});
    expect(trigger.textContent).toBe('20%');
    expect(ring.getAttribute('stroke-dasharray')).toBe('20 100');
  }
  expect(contextCalls()).toHaveLength(1);
  await act(async () => {await vi.advanceTimersByTimeAsync(250);});
  expect(contextCalls()).toHaveLength(2);
  expect(contextCalls()[1][1]).toEqual({sessionId:'a',text:'草稿材料',attachmentIds:['complete']});
  expect(trigger.textContent).toBe('20%');
  await act(async () => {fixture.current.text='更新材料';fixture.listeners.forEach(fn=>fn());});
  await act(async () => {pending[1](status('a',9000));await vi.advanceTimersByTimeAsync(350);});
  expect(trigger.textContent).toBe('20%');
  await act(async () => {pending[2](status('a',3000));});
  expect(trigger.textContent).toBe('30%');
  expect(ring.getAttribute('stroke-dasharray')).toBe('30 100');
});
it('keeps the previous estimate while attachments change but reveals a refresh failure', async () => {
  vi.useFakeTimers(); let fail=false;
  const rpc=vi.fn(async(method:string,params:any)=>{
    if(method==='bootstrap')return structuredClone(boot);
    if(method==='sessions.get')return {session:session(params.sessionId),messages:[]};
    if(method==='sessions.context'){if(fail)throw Error('refresh unavailable');return status('a',2000);}
    throw Error(method);
  });
  const store=new ChatStore(rpc as Rpc);await store.initialize();const fixture=composerFixture();
  render(<ComposerStatus store={store} session={session('a')} composer={fixture.runtime} onSettings={vi.fn()}/>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(350);});
  fail=true;
  await act(async()=>{fixture.current.attachments=[];fixture.listeners.forEach(fn=>fn());});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('20%');
  await act(async()=>{await vi.advanceTimersByTimeAsync(350);});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('—');
  expect(screen.getByRole('alert').textContent).toBe('refresh unavailable');
});
it('invalidates the old budget and pending replies when model settings change',async()=>{
  vi.useFakeTimers();const pending:((value:ContextStatus)=>void)[]=[];
  const settings=structuredClone(boot.settings);settings.api.model='new-model';
  const rpc=vi.fn(async(method:string,params:any)=>{
    if(method==='bootstrap')return structuredClone(boot);
    if(method==='sessions.get')return {session:session(params.sessionId),messages:[]};
    if(method==='sessions.context')return new Promise<ContextStatus>(resolve=>pending.push(resolve));
    if(method==='settings.save')return settings;
    throw Error(method);
  });
  const store=new ChatStore(rpc as Rpc);await store.initialize();const fixture=composerFixture();
  render(<ComposerStatus store={store} session={session('a')} composer={fixture.runtime} onSettings={vi.fn()}/>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(350);pending[0](status('a',2000));});
  await act(async()=>{fixture.current.text='changed';fixture.listeners.forEach(fn=>fn());});
  await act(async()=>{await vi.advanceTimersByTimeAsync(350);});
  await act(async()=>{await store.save({api:{model:'new-model'}});});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('…');
  expect(screen.getByRole('button',{name:'切换模型与推理强度'}).textContent).toContain('new-model');
  await act(async()=>{pending[1](status('a',9000));await vi.advanceTimersByTimeAsync(350);});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('…');
  await act(async()=>{pending[2]({...status('a',3000),model:'new-model'});});
  expect(screen.getByRole('button',{name:'上下文用量'}).textContent).toBe('30%');
});
it('failed quick-save shows an error without optimistic configuration changes', async () => {
  const rpc=vi.fn(async(method:string,params:any)=>{
    if(method==='bootstrap')return structuredClone(boot);
    if(method==='sessions.get')return {session:session(params.sessionId),messages:[]};
    if(method==='sessions.context')return status('a',2000);
    if(method==='settings.save')throw Error('save refused');
    throw Error(method);
  });
  const store=new ChatStore(rpc as Rpc);await store.initialize();const fixture=composerFixture();
  render(<ComposerStatus store={store} session={session('a')} composer={fixture.runtime} onSettings={vi.fn()}/>);
  await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'低'}));});
  expect(screen.getByRole('alert').textContent).toBe('save refused');
  expect(screen.getByRole('button',{name:'切换模型与推理强度'}).textContent).toContain('api-model · high');
  expect(rpc.mock.calls.find(([method])=>method==='settings.save')?.[1]).toEqual({settings:{api:{thinking_level:'low',force_deep_thinking:false}}});
});
