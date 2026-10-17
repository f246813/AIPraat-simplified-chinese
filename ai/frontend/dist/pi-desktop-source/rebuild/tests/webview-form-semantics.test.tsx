import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {SettingsDashboard} from '../src/Settings';
import {createDemoAdapter} from '../src/demo';
import {ChatStore} from '../src/store';
import type {Rpc} from '../src/types';

afterEach(cleanup);
it('square lock is an explicit styling exception, not a different business control',async()=>{
  const rpc=vi.fn(createDemoAdapter()) as unknown as Rpc;
  const store=new ChatStore(rpc); await store.initialize();
  render(<SettingsDashboard store={store} onClose={()=>{}}/>);
  const lock=screen.getByRole('checkbox',{name:'锁定云端 API'}) as HTMLInputElement;
  const toggle=screen.getByRole('checkbox',{name:'显式清除已保存 API Key'}) as HTMLInputElement;
  expect(lock.dataset.toggle).toBe('lock');
  expect(lock.closest('label')?.classList.contains('lock')).toBe(true);
  expect(toggle.dataset.toggle).toBeUndefined();
  expect(lock.type).toBe('checkbox'); expect(toggle.type).toBe('checkbox');
  expect(screen.queryByRole('switch')).toBeNull();
  const wasLocked=lock.checked;
  fireEvent.click(lock.closest('label')!.querySelector('span')!);
  expect(lock.checked).toBe(!wasLocked);
  fireEvent.click(toggle.closest('label')!.querySelector('span')!);
  expect(toggle.checked).toBe(true);
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'保存更改'})));
  expect(store.getSnapshot().boot?.settings.api.locked).toBe(!wasLocked);
  // Successful save redacts the transient clear-key request, preserving existing behavior.
  expect((screen.getByRole('checkbox',{name:'显式清除已保存 API Key'}) as HTMLInputElement).checked).toBe(false);
  expect((rpc as any).mock.calls.some(([method]:[string])=>method==='settings.test')).toBe(false);
  store.stop();
});
