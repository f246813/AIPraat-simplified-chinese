import {describe,it,expect,vi} from 'vitest';
import {ChatStore} from '../src/store';
import type {Bootstrap,HistorySection,Rpc,Session} from '../src/types';
const section:HistorySection={id:'section',name:'研究',appearance:null};
const session:Session={id:'a',title:'parent',readOnly:false,created:'',updated:'',draft:'saved',scroll:88};
const boot:Bootstrap={sessions:[session],sections:[section],tasks:[],providers:[],models:[],settings:{api:{},local:{},preferences:{},analysis:{},alignment:{}},host:{name:'fixture',version:'',cloudAllowed:false}};
describe('history organization snapshots',()=>{
  it('keeps a renamed title when an older preview and bootstrap return later',async()=>{
    let finishBoot!:(value:unknown)=>void,finishRead!:(value:unknown)=>void;let starts=0,reads=0;
    const rpc=vi.fn(async(method:string)=>method==='bootstrap'?(++starts===1?structuredClone(boot):new Promise(resolve=>finishBoot=resolve)):method==='sessions.get'?(++reads===2?new Promise(resolve=>finishRead=resolve):{session,messages:[]}):{ok:true});
    const store=new ChatStore(rpc as Rpc);await store.initialize();const preview=store.load('a');const refreshing=store.initialize();await store.rename('a','renamed');finishBoot(boot);await Promise.resolve();await Promise.resolve();finishRead({session,messages:[]});await preview;await refreshing;expect(store.getSnapshot().sessions[0].title).toBe('renamed');store.stop();
  });
  it('reconciles terminal tasks on event reset even when a section changes during bootstrap',async()=>{
    let finish!:(value:unknown)=>void;let starts=0;
    const running={id:'task',sessionId:'a',status:'running',model:'fixture',created:''};
    const rpc=vi.fn(async(method:string)=>method==='bootstrap'?(++starts===1?structuredClone({...boot,tasks:[running]}):new Promise(resolve=>finish=resolve)):method==='sections.create'?{...section,id:'new'}:method==='events.poll'?{events:[],cursor:40,reset:true}:{session,messages:[]});
    const store=new ChatStore(rpc as Rpc);await store.initialize();expect(store.running('a')).toBeDefined();
    const reset=store.pollOnce();await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));await store.createSection('new');finish({...boot,tasks:[{...running,status:'complete'}]});await reset;
    expect(store.running('a')).toBeUndefined();expect(store.getSnapshot().boot?.sections?.some(s=>s.id==='new')).toBe(true);store.stop();
  });
  it.each(['sessions.pin','sessions.create','sessions.delete','sessions.rename'] as const)('serializes %s behind pending organization snapshots',async(method)=>{
    const b={...session,id:'b',title:'b',sectionId:null,pinned:false};let database=[{...session,sectionId:null},b] as Session[];
    let finish!:(value:unknown)=>void;
    const created={...session,id:'created',title:'created',draft:'',scroll:0};
    const rpc=vi.fn(async(name:string,p:any)=>{
      if(name==='bootstrap')return structuredClone({...boot,sessions:database});
      if(name==='sessions.get')return {session:structuredClone(database.find(s=>s.id===p.sessionId)),messages:[]};
      if(name==='sessions.section'){database=database.map(s=>s.id==='a'?{...s,sectionId:'section',sectionPosition:1000000}:s);const snapshot=structuredClone({sessions:database,sections:[section]});return new Promise(resolve=>{finish=()=>resolve(snapshot);});}
      if(name==='sessions.pin'){database=database.map(s=>s.id==='b'?{...s,pinned:true,sectionId:'pinned',sectionPosition:1000000}:s);return structuredClone(database.find(s=>s.id==='b'));}
      if(name==='sessions.create'){database.push(created);return created;}
      if(name==='sessions.delete')database=database.filter(s=>s.id!=='b');
      if(name==='sessions.rename')database=database.map(s=>s.id==='b'?{...s,title:'renamed'}:s);
      return {ok:true};
    });
    const store=new ChatStore(rpc as Rpc);await store.initialize();const pending=store.moveToSection('a','section');await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));
    const concurrent=method==='sessions.pin'?store.pin('b',true):method==='sessions.create'?store.create():method==='sessions.delete'?store.delete('b'):store.rename('b','renamed');
    await Promise.resolve();await Promise.resolve();expect(rpc.mock.calls.some(([name])=>name===method)).toBe(false);
    finish(undefined);await pending;await concurrent;
    if(method==='sessions.pin')expect(store.getSnapshot().sessions.find(s=>s.id==='b')).toMatchObject({pinned:true,sectionId:'pinned'});
    if(method==='sessions.create'){expect(store.getSnapshot().sessions.some(s=>s.id==='created')).toBe(true);expect(store.getSnapshot().selected).toBe('created');}
    if(method==='sessions.delete')expect(store.getSnapshot().sessions.some(s=>s.id==='b')).toBe(false);
    if(method==='sessions.rename')expect(store.getSnapshot().sessions.find(s=>s.id==='b')?.title).toBe('renamed');
    store.stop();
  });
  it('protects new membership from an older pending preview and keeps local reading state',async()=>{
    let resolve!:(value:unknown)=>void;let reads=0;
    const rpc=vi.fn(async(method:string)=>method==='bootstrap'?structuredClone(boot):method==='sessions.get'?(++reads===1?{session,messages:[]}:new Promise(r=>resolve=r)):method==='sessions.section'?{sessions:[{...session,sectionId:'section',sectionPosition:1000000}],sections:[section]}:{ok:true});
    const store=new ChatStore(rpc as Rpc);await store.initialize();const pending=store.load('a');await store.moveToSection('a','section');resolve({session:{...session,draft:'stale'},messages:[]});await pending;
    expect(store.getSnapshot().sessions[0]).toMatchObject({sectionId:'section'});expect(store.getView('a')).toMatchObject({draft:'saved',scroll:88});store.stop();
  });
  it('forks by the clicked session id and selects only the new independent session',async()=>{
    const fork={...session,id:'fork',title:'parent · 分叉',forkedFrom:'a',draft:'',scroll:0};
    const rpc=vi.fn(async(method:string,p:any)=>method==='bootstrap'?structuredClone(boot):method==='sessions.fork'?fork:method==='sessions.get'?{session:p.sessionId==='fork'?fork:session,messages:[]}:{ok:true});
    const store=new ChatStore(rpc as Rpc);await store.initialize();await store.fork('a');expect(rpc).toHaveBeenCalledWith('sessions.fork',{sessionId:'a'});expect(store.getSnapshot().selected).toBe('fork');expect(store.getSnapshot().sessions.find(s=>s.id==='a')?.draft).toBe('saved');store.stop();
  });
  it('does not let a stale bootstrap erase a section created during its request',async()=>{
    let finish!:(value:unknown)=>void;let starts=0;
    const rpc=vi.fn(async(method:string)=>method==='bootstrap'?(++starts===1?structuredClone(boot):new Promise(r=>finish=r)):method==='sections.create'?{...section,id:'new',name:'new'}:{session,messages:[]});
    const store=new ChatStore(rpc as Rpc);await store.initialize();const initializing=store.initialize();await store.createSection('new');finish(boot);await initializing;expect(store.getSnapshot().boot?.sections?.map(s=>s.id)).toEqual(['section','new']);store.stop();
  });
});
