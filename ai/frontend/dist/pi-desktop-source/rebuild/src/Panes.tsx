/**
 * Pane separators: the expanded-sidebar edge and the two conversation-band
 * handles. Interaction ported from PI-Desktop (fixed revision
 * `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0); see `layout.ts` for
 * the adopted bounds and for why persistence goes through the host settings.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from 'react';
import { ChatStore, useChatSelector } from './store';
import {
  clampConversationWidth, clampSidebarWidth, conversationWidthFromDrag, resizeKeyIntent, resizeStep,
  sidebarPointerResize, sidebarResetWidth, sidebarWidthBudget,
  CONVERSATION_WIDTH_DEFAULT, CONVERSATION_WIDTH_MIN, SIDEBAR_WIDTH_MIN,
} from './layout';

type SidebarGesture = {pointerId: number; startWidth: number; startClientX: number; maxWidth: number; current: number; frame: number};
type BandGesture = {pointerId: number; side: 'left' | 'right'; startWidth: number; startClientX: number; paneWidth: number; current: number; frame: number};

/** WebView2 runs in private mode and loses localStorage on close: host settings are the only durable store. */
export function useStoredWidth(store: ChatStore, key: 'sidebar_width' | 'conversation_width', normalize: (value: unknown) => number) {
  // Subscribe to the single persisted preference this hook consumes; a primitive
  // selector means unrelated state (message streaming, other sessions) never
  // re-renders the App or Chat host.
  const pref = useChatSelector(store, state => (state.boot?.settings.preferences as Record<string, unknown> | undefined)?.[key]);
  const stored = normalize(pref);
  // A live gesture owns the width until the page reloads; the host value is the floor truth.
  const [override, setOverride] = useState<number>();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const queued = useRef<number | undefined>(undefined);
  const flush = useCallback(() => {
    clearTimeout(timer.current);
    const value = queued.current; queued.current = undefined;
    if (value === undefined) return;
    // A failed write keeps the live width on screen; the next gesture retries.
    void store.save({preferences: {[key]: value}}).catch(store.report);
  }, [store, key]);
  const commit = useCallback((value: number) => {
    setOverride(value); queued.current = value; clearTimeout(timer.current);
    timer.current = setTimeout(flush, 350);
  }, [flush]);
  const preview = useCallback((value: number) => setOverride(value), []);
  useEffect(() => () => flush(), [flush]);
  return {width: override ?? stored, preview, commit};
}

function useResizingFlag() {
  const active = useRef(false);
  const [state, setState] = useState(false);
  const set = useCallback((on: boolean) => {
    active.current = on; setState(on);
    if (on) document.documentElement.dataset.paneResizing = 'true';
    else delete document.documentElement.dataset.paneResizing;
  }, []);
  // A separator unmounted mid-gesture must not leave the whole page in drag styling.
  useEffect(() => () => { if (active.current) delete document.documentElement.dataset.paneResizing; }, []);
  return [state, set] as const;
}

/** Expanded-sidebar separator. Collapsed, the rail is a fixed 68px and this is not rendered. */
export function SidebarResizer({width, maxWidth, onPreview, onCommit, onCollapse}: {
  width: number; maxWidth: number;
  onPreview: (width: number) => void; onCommit: (width: number) => void; onCollapse: () => void;
}) {
  const drag = useRef<SidebarGesture | undefined>(undefined);
  const [resizing, setResizing] = useResizingFlag();
  const finish = useCallback((target: HTMLDivElement, pointerId: number, cancelled: boolean) => {
    const gesture = drag.current;
    if (gesture?.pointerId !== pointerId) return;
    if (gesture.frame) cancelAnimationFrame(gesture.frame);
    drag.current = undefined;
    if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
    setResizing(false);
    if (cancelled) onPreview(gesture.startWidth); else onCommit(gesture.current);
  }, [onCommit, onPreview, setResizing]);
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.focus({preventScroll: true});
    const start = clampSidebarWidth(width, maxWidth);
    drag.current = {pointerId: event.pointerId, startWidth: start, startClientX: event.clientX, maxWidth, current: start, frame: 0};
    setResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (gesture?.pointerId !== event.pointerId) return;
    const next = sidebarPointerResize({startWidth: gesture.startWidth, deltaX: event.clientX - gesture.startClientX, maxWidth: gesture.maxWidth});
    // A cramped column is worse than a collapsed rail; the preferred width stays in state.
    if (next.type === 'collapse') { finish(event.currentTarget, event.pointerId, true); onCollapse(); return; }
    gesture.current = next.width;
    if (gesture.frame) return;
    gesture.frame = requestAnimationFrame(() => { if (drag.current !== gesture) return; gesture.frame = 0; onPreview(gesture.current); });
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => finish(event.currentTarget, event.pointerId, false);
  const onPointerCancel = (event: ReactPointerEvent<HTMLDivElement>) => finish(event.currentTarget, event.pointerId, true);
  const onDoubleClick = () => onCommit(sidebarResetWidth(maxWidth));
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (event.key === 'Escape' && gesture) { event.preventDefault(); finish(event.currentTarget, gesture.pointerId, true); return; }
    const intent = resizeKeyIntent(event, 'ArrowRight');
    if (!intent) return;
    event.preventDefault();
    if (intent === 'default') return onCommit(sidebarResetWidth(maxWidth));
    if (intent === 'maximum') return onCommit(clampSidebarWidth(maxWidth, maxWidth));
    onCommit(clampSidebarWidth(width + (intent === 'wider' ? resizeStep(event.shiftKey) : -resizeStep(event.shiftKey)), maxWidth));
  };
  return <div className={`sidebar-resizer ${resizing ? 'is-resizing' : ''}`} role="separator" aria-orientation="vertical" aria-label="调整会话侧栏宽度" aria-valuemin={SIDEBAR_WIDTH_MIN} aria-valuemax={Math.round(maxWidth)} aria-valuenow={Math.round(width)} tabIndex={0} title="拖动调整侧栏宽度 · 双击还原" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel} onDoubleClick={onDoubleClick} onKeyDown={onKeyDown}/>;
}

/** Dual edge handles for the centered conversation band: both sides move one shared preferred width. */
export function ConversationWidthHandles({viewport, width, onPreview, onCommit}: {
  viewport: RefObject<HTMLDivElement | null>; width: number;
  onPreview: (width: number) => void; onCommit: (width: number) => void;
}) {
  const drag = useRef<BandGesture | undefined>(undefined);
  const [resizing, setResizing] = useResizingFlag();
  const [paneWidth, setPaneWidth] = useState(0);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const sync = () => setPaneWidth(node.clientWidth);
    sync();
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', sync); return () => window.removeEventListener('resize', sync); }
    const observer = new ResizeObserver(sync); observer.observe(node);
    return () => observer.disconnect();
  }, [viewport]);
  const finish = useCallback((target: HTMLDivElement, pointerId: number, cancelled: boolean) => {
    const gesture = drag.current;
    if (gesture?.pointerId !== pointerId) return;
    if (gesture.frame) cancelAnimationFrame(gesture.frame);
    drag.current = undefined;
    if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
    setResizing(false);
    if (cancelled) onPreview(gesture.startWidth); else onCommit(gesture.current);
  }, [onCommit, onPreview, setResizing]);
  const onPointerDown = (side: 'left' | 'right') => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.focus({preventScroll: true});
    const pane = viewport.current?.clientWidth || paneWidth;
    const start = clampConversationWidth(width, pane);
    drag.current = {pointerId: event.pointerId, side, startWidth: start, startClientX: event.clientX, paneWidth: pane, current: start, frame: 0};
    onPreview(start); setResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (gesture?.pointerId !== event.pointerId) return;
    gesture.current = conversationWidthFromDrag({side: gesture.side, startWidth: gesture.startWidth, startClientX: gesture.startClientX, clientX: event.clientX, paneWidth: gesture.paneWidth});
    if (gesture.frame) return;
    gesture.frame = requestAnimationFrame(() => { if (drag.current !== gesture) return; gesture.frame = 0; onPreview(gesture.current); });
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => finish(event.currentTarget, event.pointerId, false);
  const onPointerCancel = (event: ReactPointerEvent<HTMLDivElement>) => finish(event.currentTarget, event.pointerId, true);
  const onDoubleClick = () => onCommit(CONVERSATION_WIDTH_DEFAULT);
  const onKeyDown = (side: 'left' | 'right') => (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (event.key === 'Escape' && gesture) { event.preventDefault(); finish(event.currentTarget, gesture.pointerId, true); return; }
    const intent = resizeKeyIntent(event, side === 'left' ? 'ArrowLeft' : 'ArrowRight');
    if (!intent) return;
    event.preventDefault();
    const livePane = viewport.current?.clientWidth || paneWidth;
    if (intent === 'default') return onCommit(CONVERSATION_WIDTH_DEFAULT);
    if (intent === 'maximum') return onCommit(clampConversationWidth(Number.POSITIVE_INFINITY, livePane));
    // Keyboard steps are plain pixels; only pointer travel is doubled by the centered band.
    const step = resizeStep(event.shiftKey);
    onCommit(clampConversationWidth(width + (intent === 'wider' ? step : -step), livePane));
  };
  const maximum = clampConversationWidth(Number.POSITIVE_INFINITY, paneWidth || 10000);
  return <div className="chat-width-handles">
    <div className="chat-width-band">
      {(['left', 'right'] as const).map(side => <div key={side} role="separator" aria-orientation="vertical" aria-label="调整对话区宽度" aria-valuemin={CONVERSATION_WIDTH_MIN} aria-valuemax={Math.round(maximum)} aria-valuenow={Math.round(width)} aria-valuetext={`${Math.round(width)} 像素`} data-side={side} tabIndex={0} className={`chat-width-handle chat-width-handle-${side} ${resizing ? 'is-resizing' : ''}`} title="拖动调整对话区宽度 · 双击还原" onPointerDown={onPointerDown(side)} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel} onDoubleClick={onDoubleClick} onKeyDown={onKeyDown(side)}/>)}
    </div>
  </div>;
}

/** Live sidebar budget: the chat column keeps its floor, so the sidebar yields first. */
export function useSidebarBudget(container: RefObject<HTMLElement | null>) {
  const [budget, setBudget] = useState(() => sidebarWidthBudget(0));
  useLayoutEffect(() => {
    const node = container.current;
    if (!node) return;
    const sync = () => setBudget(sidebarWidthBudget(node.clientWidth));
    sync();
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', sync); return () => window.removeEventListener('resize', sync); }
    const observer = new ResizeObserver(sync); observer.observe(node);
    return () => observer.disconnect();
  }, [container]);
  return budget;
}
