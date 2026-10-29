import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {useSmoothText} from '../src/pi/useSmoothText';

let now: number, serial: number, frames: Map<number, FrameRequestCallback>;
let media: EventTarget & {matches: boolean};
beforeEach(() => {
  now = 100; serial = 0; frames = new Map();
  media = Object.assign(new EventTarget(), {matches:false});
  vi.stubGlobal('matchMedia', () => media);
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {frames.set(++serial, callback); return serial;});
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});
function step(ms = 20) {
  act(() => {now += ms; const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now));});
}

it('releases a new complete reply progressively and stops once the text is shown', () => {
  const text = '这是一条一次性交付的新回复。'.repeat(12);
  const {result, unmount} = renderHook(() => useSmoothText(text, true, true));
  expect(result.current).toBe('');
  step();
  expect(result.current.length).toBeGreaterThan(0);
  expect(result.current.length).toBeLessThan(text.length / 4);
  const first = result.current;
  step(); expect(result.current.startsWith(first)).toBe(true);
  for (let i = 0; i < 150; i++) step();
  expect(result.current).toBe(text); expect(frames.size).toBe(0);
  unmount(); expect(frames.size).toBe(0);
});

it('paces small incoming chunks instead of dumping four characters per frame', () => {
  const {result, rerender} = renderHook(({text}) => useSmoothText(text, true, true), {initialProps:{text:''}});
  rerender({text:'这是分批到达的文字。'});
  step(8); expect(result.current).toBe('');
  step(12); expect(result.current).toBe('这');
  rerender({text:'这是分批到达的文字。下一句也需要平滑。'});
  step(); expect(result.current).toBe('这是');
});

it('shows loaded history immediately and animates only subsequent appends', () => {
  const {result, rerender} = renderHook(({text}) => useSmoothText(text, true), {initialProps:{text:'已有的历史文字'}});
  expect(result.current).toBe('已有的历史文字');
  rerender({text:'已有的历史文字，新收到的追加文字'});
  expect(result.current).toBe('已有的历史文字');
  step(); expect(result.current).toBe('已有的历史文字，');
});

it('flushes when disabled or reduced motion changes and cancels on unmount', () => {
  const {result, rerender, unmount} = renderHook(({enabled}) => useSmoothText('待显示的完整回复'.repeat(20), enabled, true), {initialProps:{enabled:true}});
  step(); expect(result.current.length).toBeLessThan(160);
  act(() => {media.matches = true; media.dispatchEvent(new Event('change'));});
  expect(result.current).toBe('待显示的完整回复'.repeat(20));
  expect(frames.size).toBe(0);
  rerender({enabled:false}); expect(frames.size).toBe(0);
  unmount(); expect(frames.size).toBe(0);
});

it('replaces shortened or corrected text immediately without an old prefix', () => {
  const {result, rerender} = renderHook(({text}) => useSmoothText(text, true, true), {initialProps:{text:'较长的原始回复'.repeat(20)}});
  step(); rerender({text:'修正后的回复'});
  expect(result.current).toBe('修正后的回复');
  expect(frames.size).toBe(0);
});

it('keeps emoji surrogate pairs intact and skips animation for oversized text', () => {
  const {result, rerender} = renderHook(({text}) => useSmoothText(text, true, true), {initialProps:{text:'😀这是一条回复'}});
  step(); expect(result.current).toBe('😀');
  rerender({text:'超'.repeat(32769)}); expect(result.current.length).toBe(32769);
  expect(frames.size).toBe(0);
});
