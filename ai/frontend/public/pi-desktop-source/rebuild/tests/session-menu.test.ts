import {afterEach, expect, it, vi} from 'vitest';
import {ChatStore} from '../src/store';
import {HostBridge} from '../src/bridge';
import {createDemoAdapter} from '../src/demo';
import {installScrollbarReveal} from '../src/pi/scrollbar-reveal';
import {placeContextMenu} from '../src/pi/context-menu';
afterEach(() => vi.useRealTimers());
it.each([false,true])('deletes readonly legacy sessions (archived=%s) and keeps them absent after refresh', async archived => {
  const bridge=new HostBridge(createDemoAdapter());const store=new ChatStore(bridge.rpc);
  await store.initialize();if(archived)await store.archive('legacy:fixture',true);
  await store.select('legacy:fixture');await store.delete('legacy:fixture');
  expect(store.getSnapshot().sessions.some(s=>s.id==='legacy:fixture')).toBe(false);
  expect(store.getSnapshot().selected).not.toBe('legacy:fixture');
  await store.initialize();expect(store.getSnapshot().sessions.some(s=>s.id==='legacy:fixture')).toBe(false);
  store.stop();
});
it('pins authoritatively, preserves local views/attachments/messages and restores on bootstrap', async () => {
  const bridge = new HostBridge(createDemoAdapter()); const store = new ChatStore(bridge.rpc);
  await store.initialize(); store.view('demo-history',{draft:'local',scroll:142});
  store.attachments.set('demo-history',[{id:'file',name:'x',size:1,mime:'text/plain'}]);
  const messages = store.getSnapshot().messages;
  await store.pin('demo-history',true);
  expect(store.getSnapshot().sessions[0]).toMatchObject({id:'demo-history',pinned:true});
  expect(store.getView('demo-history')).toMatchObject({draft:'local',scroll:142});
  expect(store.getSnapshot().messages).toBe(messages); expect(store.attachments.get('demo-history')).toHaveLength(1);
  const restored = new ChatStore(bridge.rpc); await restored.initialize(); expect(restored.getSnapshot().sessions[0].pinned).toBe(true);
  await store.pin('demo-history',false); expect(store.getSnapshot().sessions.find(s=>s.id==='demo-history')?.pinned).toBe(false);
  store.stop(); restored.stop();
});
it('failed pin leaves order and metadata unchanged; legacy never writes', async () => {
  const demo = createDemoAdapter(); const rpc = vi.fn(async(method:any,params:any) => {
    if(method==='sessions.pin') throw new Error('保存失败'); return demo(method,params);
  });
  const store = new ChatStore(rpc as typeof demo); await store.initialize(); const sessions = store.getSnapshot().sessions;
  await expect(store.pin('demo-history',true)).rejects.toThrow('保存失败'); expect(store.getSnapshot().sessions).toBe(sessions);
  rpc.mockClear(); await expect(store.pin('legacy:fixture',true)).rejects.toThrow('只读'); expect(rpc).not.toHaveBeenCalled(); store.stop();
});
it('scroll reveal resets 300ms hold and cleans timers/attributes/listener', () => {
  vi.useFakeTimers(); const element=document.createElement('div'); document.body.append(element);
  const dispose=installScrollbarReveal(document); element.dispatchEvent(new Event('scroll'));
  expect(element.hasAttribute('data-scrolling')).toBe(true); vi.advanceTimersByTime(250);
  element.dispatchEvent(new Event('scroll')); vi.advanceTimersByTime(299); expect(element.hasAttribute('data-scrolling')).toBe(true);
  vi.advanceTimersByTime(1); expect(element.hasAttribute('data-scrolling')).toBe(false);
  element.dispatchEvent(new Event('scroll')); dispose(); expect(element.hasAttribute('data-scrolling')).toBe(false);
  element.dispatchEvent(new Event('scroll')); expect(element.hasAttribute('data-scrolling')).toBe(false); element.remove();
});
it('clamps menu to viewport margins', () => {
  const p=placeContextMenu({x:389,y:699},{width:184,height:130},{width:390,height:700});
  expect(p.left).toBeGreaterThanOrEqual(8); expect(p.left+184).toBeLessThanOrEqual(382);
  expect(p.top+130).toBeLessThanOrEqual(692);
});
it('an older background snapshot cannot undo a newly acknowledged pin', async () => {
  const demo=createDemoAdapter(); let resolve!:(value:any)=>void; let defer=false;
  const rpc=async(method:any,params:any)=>{
    if(method==='sessions.get' && defer) { const old=await demo(method,params); return new Promise(r=>{resolve=()=>r(old);}); }
    return demo(method,params);
  };
  const store=new ChatStore(rpc as typeof demo); await store.initialize(); defer=true;
  const loading=store.load('demo-history'); await Promise.resolve(); await Promise.resolve();
  await store.pin('demo-history',true); resolve(null); await loading;
  expect(store.getSnapshot().sessions[0]).toMatchObject({id:'demo-history',pinned:true});store.stop();
});
