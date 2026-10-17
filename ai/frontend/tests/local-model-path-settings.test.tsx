import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {SettingsDashboard} from '../src/Settings';
import {ChatStore} from '../src/store';
import {createDemoAdapter} from '../src/demo';
import {HostBridge} from '../src/bridge';
import type {Rpc} from '../src/types';

afterEach(cleanup);
it('local connection settings offer a model picker and save its path',async()=>{
  const rpc=vi.fn(createDemoAdapter()) as unknown as Rpc;
  const store=new ChatStore(new HostBridge(rpc).rpc);await store.initialize();
  render(<SettingsDashboard store={store} onClose={()=>{}}/>);
  expect(screen.queryByRole('button',{name:'添加本地模型'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'本地模型'}));
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'添加本地模型'})));
  expect(screen.getByText('browser-fixture.gguf').getAttribute('title')).toBe('D:\\models\\browser-fixture.gguf');
  expect((rpc as any).mock.calls.some(([method]:[string])=>method==='settings.save')).toBe(false);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'保存更改'})));
  const save=(rpc as any).mock.calls.find(([method]:[string])=>method==='settings.save');
  expect(save[1].settings.server.model_path).toBe('D:\\models\\browser-fixture.gguf');
  expect(save[1].settings.api.enabled).toBe(false);
  expect((rpc as any).mock.calls.some(([method]:[string])=>['tasks.submit','settings.test'].includes(method))).toBe(false);
  store.stop();
});
it('canceling the native picker preserves the selected unsaved model',async()=>{
  const demo=createDemoAdapter();
  let choices=0;
  const rpc=(async(method:string,params:any)=>method==='settings.choose_model'?{path:++choices===1?'D:\\models\\chosen.gguf':null}:demo(method as any,params)) as Rpc;
  const store=new ChatStore(new HostBridge(rpc).rpc);await store.initialize();
  render(<SettingsDashboard store={store} onClose={()=>{}}/>);
  fireEvent.click(screen.getByRole('button',{name:'本地模型'}));
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'添加本地模型'})));
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'添加本地模型'})));
  expect(screen.getByText('chosen.gguf').getAttribute('title')).toBe('D:\\models\\chosen.gguf');
  store.stop();
});
