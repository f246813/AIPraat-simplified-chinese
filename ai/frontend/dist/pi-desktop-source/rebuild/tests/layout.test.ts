import { expect, it } from 'vitest';
import { ChatStore } from '../src/store';
import { HostBridge } from '../src/bridge';
import { createDemoAdapter } from '../src/demo';
import {
  clampConversationWidth, clampSidebarWidth, conversationWidthFromDrag, resizeKeyIntent, resizeStep,
  resolveConversationWidth, sidebarPointerResize, sidebarResetWidth, sidebarWidthBudget,
  CONVERSATION_WIDTH_DEFAULT, CONVERSATION_WIDTH_MIN, MAIN_PANE_MIN_WIDTH,
  SIDEBAR_WIDTH_DEFAULT, SIDEBAR_WIDTH_MAX, SIDEBAR_WIDTH_MIN,
} from '../src/layout';

it('settles an absent or corrupt sidebar width on the default instead of writing NaN', () => {
  for (const value of [undefined, null, '300', Number.NaN, Number.POSITIVE_INFINITY]) expect(clampSidebarWidth(value)).toBe(SIDEBAR_WIDTH_DEFAULT);
  expect(clampSidebarWidth(300)).toBe(300);
  expect(clampSidebarWidth(300.4)).toBe(300);
  expect(clampSidebarWidth(10)).toBe(SIDEBAR_WIDTH_MIN);
  expect(clampSidebarWidth(9000)).toBe(SIDEBAR_WIDTH_MAX);
  // A live budget below the minimum must still leave a usable column.
  expect(clampSidebarWidth(400, 300)).toBe(300);
  expect(clampSidebarWidth(400, 10)).toBe(SIDEBAR_WIDTH_MIN);
});

it('caps the sidebar so the chat column keeps its floor', () => {
  expect(sidebarWidthBudget(1200)).toBe(SIDEBAR_WIDTH_MAX);
  expect(sidebarWidthBudget(800)).toBe(800 - MAIN_PANE_MIN_WIDTH - 1);
  expect(sidebarWidthBudget(0)).toBe(SIDEBAR_WIDTH_MAX);
  expect(sidebarWidthBudget(400)).toBe(SIDEBAR_WIDTH_MIN);
});

it('collapses rather than leaving a cramped expanded column, without rewriting the preferred width', () => {
  expect(sidebarPointerResize({startWidth: 300, deltaX: 40})).toEqual({type: 'width', width: 340});
  expect(sidebarPointerResize({startWidth: 300, deltaX: -141})).toEqual({type: 'collapse'});
  expect(sidebarPointerResize({startWidth: 300, deltaX: -140})).toEqual({type: 'width', width: SIDEBAR_WIDTH_MIN});
  expect(sidebarPointerResize({startWidth: 300, deltaX: 400, maxWidth: 520})).toEqual({type: 'width', width: 520});
  expect(sidebarPointerResize({startWidth: Number.NaN, deltaX: 10})).toEqual({type: 'width', width: SIDEBAR_WIDTH_DEFAULT});
  expect(sidebarPointerResize({startWidth: 300, deltaX: Number.NaN})).toEqual({type: 'width', width: SIDEBAR_WIDTH_DEFAULT});
  expect(sidebarResetWidth()).toBe(SIDEBAR_WIDTH_DEFAULT);
  expect(sidebarResetWidth(260)).toBe(260);
});

it('keeps one conversation band inside the live pane and the 24px gutters', () => {
  expect(CONVERSATION_WIDTH_DEFAULT).toBe(720);
  expect(resolveConversationWidth(undefined)).toBe(CONVERSATION_WIDTH_DEFAULT);
  expect(resolveConversationWidth(850)).toBe(850); // keep existing user dimensions, not a migration
  expect(resolveConversationWidth('900')).toBe(CONVERSATION_WIDTH_DEFAULT);
  expect(resolveConversationWidth(100)).toBe(CONVERSATION_WIDTH_MIN);
  expect(resolveConversationWidth(901.6)).toBe(902);
  expect(clampConversationWidth(900, 2000)).toBe(900);
  expect(clampConversationWidth(900, 800)).toBe(800 - 48);
  // A pane narrower than the minimum still yields: the band may not overflow its gutters.
  expect(clampConversationWidth(900, 400)).toBe(400 - 48);
  expect(clampConversationWidth(900, 0)).toBe(CONVERSATION_WIDTH_MIN);
});

it('doubles pointer travel because both handles move the same centered band', () => {
  const base = {startWidth: 800, startClientX: 500, paneWidth: 2000};
  expect(conversationWidthFromDrag({...base, side: 'right', clientX: 550})).toBe(900);
  expect(conversationWidthFromDrag({...base, side: 'left', clientX: 450})).toBe(900);
  expect(conversationWidthFromDrag({...base, side: 'right', clientX: 400})).toBe(600);
  expect(conversationWidthFromDrag({...base, side: 'right', clientX: 5000})).toBe(2000 - 48);
});

it('maps one keyboard contract onto both separator orientations', () => {
  expect(resizeStep(false)).toBe(16);
  expect(resizeStep(true)).toBe(32);
  expect(resizeKeyIntent({key: 'ArrowRight'}, 'ArrowRight')).toBe('wider');
  expect(resizeKeyIntent({key: 'ArrowLeft'}, 'ArrowRight')).toBe('narrower');
  expect(resizeKeyIntent({key: 'ArrowLeft'}, 'ArrowLeft')).toBe('wider');
  expect(resizeKeyIntent({key: 'ArrowRight'}, 'ArrowLeft')).toBe('narrower');
  for (const toward of ['ArrowLeft', 'ArrowRight'] as const) {
    expect(resizeKeyIntent({key: 'Home'}, toward)).toBe('default');
    expect(resizeKeyIntent({key: 'End'}, toward)).toBe('maximum');
    expect(resizeKeyIntent({key: 'Escape'}, toward)).toBeNull();
    expect(resizeKeyIntent({key: 'Tab'}, toward)).toBeNull();
  }
});

it('persists a layout width as one partial preferences patch and keeps every other preference', async () => {
  const bridge = new HostBridge(createDemoAdapter()); const store = new ChatStore(bridge.rpc);
  await store.initialize();
  expect(store.getSnapshot().boot!.settings.preferences).toMatchObject({sidebar_width: 272, conversation_width: 850, theme: 'system', smooth_stream: true});
  const saved = await store.save({preferences: {sidebar_width: 300}});
  expect(saved.preferences).toMatchObject({sidebar_width: 300, conversation_width: 850, theme: 'system', send_key: 'enter', smooth_stream: true});
  expect(store.getSnapshot().boot!.settings.preferences).toMatchObject({sidebar_width: 300});
  store.stop();
});
