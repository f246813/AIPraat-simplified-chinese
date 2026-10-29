/**
 * The virtual message list and the per-frame viewport coordinator.
 *
 * This is the engine half of the scroll design: a single TanStack Virtualizer bounds the
 * physical mount to the viewport while the logical range (`transcript-window.ts`) decides how
 * far up the reader has expanded. Row sizes come from `measureElement` and a shared ResizeObserver,
 * never from a scan of every `[data-message-id]`; prepending older history is
 * compensated by the engine's own key-stable anchoring, so there is no `scrollHeight` delta
 * arithmetic left in the host.
 *
 * One `requestAnimationFrame` batch owns all scroll-derived work — reading anchor, the reach-top
 * reveal, the follow-at-end flag — so a burst of native scroll events collapses into a single
 * pass. Explicit navigation (rail click, search jump, back-to-latest, restore) goes through
 * `position` and replaces the current request: a newer gesture, session, or request invalidates an
 * in-flight one, and a target outside the logical range first asks the host to open it, then
 * lands once the row measures.
 *
 * The engine talks to the outside world through the imperative handle: the minimap rail and the
 * search bar read individual row positions (engine metadata, estimate-backed for unmeasured rows) rather
 * than the DOM, and both reach withheld history through `revealTo` so the logical range opens to
 * the target before it is positioned.
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { Message, ReadingAnchor } from './types';
import { HISTORY_REVEAL_THRESHOLD_PX, type RowOffset } from './transcript-window';

/** A row is "at the tail" within this many pixels of the bottom, so streaming may be followed. */
const FOLLOW_TOLERANCE_PX = 1;
/** How long an unmeasured navigation target keeps being re-scrolled toward before it is abandoned. */
const PENDING_MEASURE_WINDOW_MS = 600;

export type TranscriptNavigate = {behavior?: 'auto' | 'smooth'; resolveOffset?: () => number | null};
type PendingNavigation = {messageId: string; offset: number; startedAt: number; settled: number; resolveOffset?: () => number | null};

/** Imperative surface the rail, the search bar, the menu and the restore path all share. */
export type TranscriptHandle = {
  /** Row tops in one content coordinate space, for every row in the logical range. */
  rowAtOffset: (offset: number) => RowOffset | null;
  subscribe: (listener: () => void) => () => void;
  /** The rendered list container: the only DOM the in-conversation search is allowed to match. */
  element: () => HTMLElement | null;
  /** Navigate to a row's top minus `offset`; opens the logical range first when it is outside. */
  position: (messageId: string, offset: number, navigate?: TranscriptNavigate) => void;
  scrollToLatest: (navigate?: TranscriptNavigate) => void;
  /** Pre-anchor fallback: put a raw stored scroll offset back (sessions that predate anchors). */
  scrollToOffset: (offset: number) => void;
  /** Open the logical range until `fullIndex` (full-history index) is inside it. */
  revealTo: (fullIndex: number, anchor?: ReadingAnchor | null, navigate?: TranscriptNavigate) => void;
};

export function estimateMessageSize(message: Message | undefined): number {
  if (!message) return 120;
  const openProcess = Boolean((message.activities?.length || message.results?.length)) && message.status === 'running';
  if (message.role === 'user') return message.content.length > 240 ? 180 : 96;
  if (openProcess) return 520;
  if (message.activities?.length || message.results?.length) return 420;
  return Math.min(640, 160 + message.content.length * 0.4);
}

type Props = {
  viewportRef: RefObject<HTMLDivElement | null>;
  messages: Message[];
  logicalStart: number;
  /** The batched reading position: fires once per frame with the anchor under the viewport top. */
  onAnchor: (anchor: ReadingAnchor | null, scrollTop: number, atEnd: boolean) => void;
  onFlush: () => void;
  /** Reaching the top of the logical range asks the host to open another page. */
  onReachTop: () => void;
  /** The host must open the range until `fullIndex` is included, then this request re-fires. */
  onRevealTo: (fullIndex: number) => void;
  /** Whether the reader is at the tail, so streaming may or may not be followed. */
  onFollowChange: (atEnd: boolean) => void;
  /** Message ids forced to stay mounted regardless of viewport: focused, editing, open-menu rows. */
  pinnedIds?: ReadonlySet<string>;
  /** Rows to mount: `index` is the local (window-relative) index the host writes as `data-index`. */
  children: (rows: {key: string; index: number; fullIndex: number; top: number; measure: (node: HTMLElement | null) => void}[]) => ReactNode;
};

export const VirtualTranscript = forwardRef<TranscriptHandle, Props>(function VirtualTranscript(
  {viewportRef, messages, logicalStart, onAnchor, onFlush, onReachTop, onRevealTo, onFollowChange, pinnedIds, children}, ref,
) {
  const count = Math.max(0, messages.length - logicalStart);
  const ids = useMemo(() => messages.map(message => message.id), [messages]);
  const indices = useMemo(() => new Map(ids.map((id, index) => [id, index])), [ids]);
  const containerRef = useRef<HTMLDivElement>(null);
  const marginRef = useRef(0);
  // The first margin reading only names the existing layout; later ones describe a real shift.
  const seeded = useRef(false);
  // Engine-owned scroll writes still emit a native `scroll` event: this counts the ones we caused,
  // so they never masquerade as the reader scrolling and trigger the reach-top reveal.
  const syntheticScroll = useRef(0);
  const pending = useRef<PendingNavigation | null>(null);
  const [targeting, setTargeting] = useState(false);
  const frame = useRef(0);
  const batchRef = useRef<() => void>(() => {});
  const listeners = useRef(new Set<() => void>());
  const schedule = useCallback((): void => {
    if (!frame.current) frame.current = requestAnimationFrame(function transcriptViewportBatch() { frame.current = 0; batchRef.current(); });
  }, []);
  const subscribe = useCallback((listener: () => void) => {
    listeners.current.add(listener);
    return () => { listeners.current.delete(listener); };
  }, []);
  const atEnd = useRef(true);
  const readingAnchor = useRef<ReadingAnchor | null>(null);
  const latchedReveal = useRef(false);
  // A reach-top reveal must come from a real scroll, not the transient first frame: before
  // `anchorTo:'end'` has positioned the scroller, `scrollTop` is briefly 0 while the tail is
  // still the reading target, so the top latch stays disarmed until the reader actually scrolls.
  const scrolled = useRef(false);

  // The scroller is the transcript viewport, but that ref is attached by the host's own
  // commit after this component's first measure effect runs, so `getScrollElement` must not
  // depend on it during that first pass: fall back to climbing from our own container, whose
  // ref is always set, to the nearest scrollable ancestor. Once the host ref lands, identity
  // is stable, so the virtualizer keeps the same element it bound to on mount.
  const resolveScrollElement = useCallback((): HTMLDivElement | null => {
    if (viewportRef.current) return viewportRef.current;
    let node = containerRef.current?.parentElement ?? null;
    while (node) {
      if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node as HTMLDivElement;
      node = node.parentElement;
    }
    return null;
  }, [viewportRef]);

  // The distance from the scroller's content origin down to the list container. Rows are painted
  // `scrollMargin` above their measurement, so the DOM never moves with this value — it only puts
  // the engine's coordinates in the same scroller space the reader's scroll offsets live in.
  const [scrollMargin, setScrollMargin] = useState(0);

  const virtualizer = useVirtualizer({
    count,
    getScrollElement: resolveScrollElement,
    estimateSize: index => estimateMessageSize(messages[logicalStart + index]),
    getItemKey: index => ids[logicalStart + index] ?? index,
    overscan: 6,
    anchorTo: targeting ? 'start' : 'end',
    followOnAppend: targeting ? false : 'auto',
    scrollEndThreshold: FOLLOW_TOLERANCE_PX,
    scrollMargin,
    useAnimationFrameWithResizeObserver: false,
    onChange: schedule,
  });
  virtualizer.shouldAdjustScrollPositionOnItemSizeChange = targeting ? () => false : undefined;

  // The offset is a layout fact, not a prop: the reveal affordance above the list comes and goes
  // with the logical range. Learning it for the first time only re-labels the coordinate space; a
  // later change moves the content under the reader's eyes, so the residual delta — the amount the
  // engine compensated with the stale margin — is written back here and the reading line is held.
  const measureMargin = useCallback((): number => {
    const scroller = resolveScrollElement(); const container = containerRef.current;
    if (!scroller || !container) return marginRef.current;
    return container.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
  }, [resolveScrollElement]);

  useLayoutEffect(() => {
    const margin = measureMargin();
    const delta = margin - marginRef.current;
    if (Math.abs(delta) < 0.5) return;
    marginRef.current = margin;
    setScrollMargin(margin);
    if (seeded.current) {
      const scroller = resolveScrollElement();
      if (scroller) { syntheticScroll.current++; virtualizer.scrollToOffset(scroller.scrollTop + delta); }
    }
    seeded.current = true;
  }, [logicalStart === 0, measureMargin, resolveScrollElement, virtualizer]);

  // `measureElement` reads the row's local index off its `data-index` attribute, so the ResizeObserver
  // can only report a height change for a node the engine recognises. The callback itself stays
  // identity-stable across renders and across range shifts: the attribute is read at measure time, so
  // a row that moves within the logical range re-registers nothing and keeps its measured size by key.
  const measureRef = useCallback((node: HTMLElement | null): void => { virtualizer.measureElement(node); }, [virtualizer]);

  // Widening the range adds rows above the reader and the engine holds the row under the reading
  // line by key. A reader already at the tail belongs *at* the tail, and the engine's own end-follow
  // only fires when the newest row changes — so seat it here. Starting a fresh command also retires
  // any clamped retry left over from an earlier write that landed short, which would otherwise be
  // replayed against the grown list and rewrite a stale absolute offset over the anchor.
  const previousStart = useRef(logicalStart);
  useLayoutEffect(() => {
    const widened = logicalStart < previousStart.current;
    previousStart.current = logicalStart;
    if (widened && atEnd.current && !pending.current) virtualizer.scrollToEnd();
  }, [logicalStart, virtualizer]);

  // Row tops in the scroller's own content coordinates — the space the durable anchor, the rail
  // and the in-conversation search all read, and the space `scrollTop` lives in. The engine's
  // measurements already include `scrollMargin`, so no DOM probe is needed to express them.
  const rowAtOffset = useCallback((offset: number): RowOffset | null => {
    virtualizer.getVirtualItems();
    const rows = virtualizer.measurementsCache;
    if (!rows.length) return null;
    let lo = 0; let hi = rows.length - 1;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (rows[mid].start <= offset) lo = mid; else hi = mid - 1;
    }
    const row = rows[lo];
    return {id: String(row.key), top: row.start};
  }, [virtualizer]);
  const finishNavigation = useCallback(() => {
    pending.current = null;
    setTargeting(false);
    latchedReveal.current = true;
  }, []);

  // One navigation target, applied toward its estimate then refined once the row measures.
  const applyPending = useCallback((): void => {
    const current = pending.current;
    if (!current) return;
    const index = indices.get(current.messageId);
    if (index === undefined) { finishNavigation(); return; }
    // Still outside the logical range: the host owns opening it; keep waiting a bounded time.
    if (index < logicalStart) {
      if (performance.now() - current.startedAt > PENDING_MEASURE_WINDOW_MS) finishNavigation();
      else schedule();
      return;
    }
    const measurement = virtualizer.measurementsCache[index - logicalStart];
    if (!measurement) { schedule(); return; }
    const node = resolveScrollElement();
    const relative = current.resolveOffset?.() ?? 0;
    const ideal = measurement.start + relative - current.offset;
    const max = node ? Math.max(0, node.scrollHeight - node.clientHeight) : 0;
    const target = Math.min(Math.max(0, ideal), max);
    // Steer toward the target on every frame; only commit the requested behaviour once the row
    // has a real height, so an estimate-driven first pass never smooth-animates to the wrong spot.
    if (node && Math.abs(node.scrollTop - target) >= FOLLOW_TOLERANCE_PX) virtualizer.scrollToOffset(target, {align: 'start', behavior: 'auto'});
    // Landing is not the reader reaching the top: the row they asked for sits there on purpose, and
    // pulling in another page would move it out from under them. Only a gesture arms the reveal again.
    const measured = virtualizer.itemSizeCache.has(current.messageId) && virtualizer.elementsCache.get(current.messageId)?.isConnected && measurement.size > 0.5;
    current.settled = measured && Math.abs((node?.scrollTop ?? 0) - target) < FOLLOW_TOLERANCE_PX ? current.settled + 1 : 0;
    if (current.settled) readingAnchor.current = {messageId: current.messageId, offset: measurement.start - (node?.scrollTop ?? 0)};
    if (current.settled >= 2 || performance.now() - current.startedAt > PENDING_MEASURE_WINDOW_MS) finishNavigation();
    else schedule();
  }, [finishNavigation, indices, logicalStart, resolveScrollElement, schedule, virtualizer]);

  const runBatch = useCallback((): void => {
    const node = resolveScrollElement();
    if (!node) return;
    const scrollTop = node.scrollTop;
    // "At the tail" has to mean the reader is looking at the newest row. Pixel distance from the end
    // alone cannot say that while rows are unmeasured: an estimate-backed total size can be shorter
    // than the real history, and then a reader in the middle of it would look like they were at the
    // end. So the newest row must itself be rendered, and only then does the tolerance apply.
    const rendered = virtualizer.getVirtualItems();
    const rendersNewest = rendered.length > 0 && rendered[rendered.length - 1].index === count - 1;
    const nowEnd = rendersNewest && virtualizer.getDistanceFromEnd() <= FOLLOW_TOLERANCE_PX;
    if (nowEnd !== atEnd.current) { atEnd.current = nowEnd; onFollowChange(nowEnd); }
    for (const listener of listeners.current) listener();
    if (pending.current) { applyPending(); return; }
    const row = rowAtOffset(scrollTop);
    readingAnchor.current = row ? {messageId: row.id, offset: row.top - scrollTop} : null;
    onAnchor(readingAnchor.current, scrollTop, nowEnd);
    if (scrolled.current && scrollTop <= HISTORY_REVEAL_THRESHOLD_PX && logicalStart > 0 && !latchedReveal.current) {
      latchedReveal.current = true;
      onReachTop();
    }
  }, [applyPending, count, logicalStart, onAnchor, onFollowChange, onReachTop, rowAtOffset, resolveScrollElement, virtualizer]);
  batchRef.current = runBatch;

  // Re-arm the reach-top latch and re-evaluate any owed navigation whenever the range or history moves.
  useEffect(() => { latchedReveal.current = false; schedule(); }, [logicalStart, messages.length, schedule]);

  useLayoutEffect(() => {
    const node = resolveScrollElement();
    if (!node) return;
    const onScroll = () => {
      if (syntheticScroll.current > 0) syntheticScroll.current--;
      else scrolled.current = true;
      schedule();
    };
    // A reader action retires an in-flight restore, search jump or reveal: the engine must not keep
    // steering back to a target the person has already left. The scroll event itself cannot tell the
    // two apart, so the gesture — wheel, pointer, touch, key — is what cancels.
    const onGesture = () => {
      // The reader owns the reading line from now on: an in-flight navigation is retired and the
      // boundary reveal is re-armed, because "reaching the top" has to mean they scrolled there —
      // not that an explicit jump landed at the edge of the range and asked for another page.
      latchedReveal.current = false;
      scrolled.current = true;
      if (pending.current) finishNavigation();
      latchedReveal.current = false;
      virtualizer.cancelScroll();
      schedule();
    };
    for (const type of ['wheel', 'pointerdown', 'touchstart', 'keydown'] as const) {
      node.addEventListener(type, onGesture, {passive: true});
    }
    node.addEventListener('scroll', onScroll, {passive: true});
    schedule();
    return () => {
      for (const type of ['wheel', 'pointerdown', 'touchstart', 'keydown'] as const) {
        node.removeEventListener(type, onGesture);
      }
      node.removeEventListener('scroll', onScroll);
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [finishNavigation, resolveScrollElement, schedule, virtualizer]);

  const flushRef = useRef(() => {});
  flushRef.current = () => {
    const node = resolveScrollElement();
    if (node && !pending.current) {
      const row = rowAtOffset(node.scrollTop);
      onAnchor(row ? {messageId: row.id, offset: row.top - node.scrollTop} : null, node.scrollTop, atEnd.current);
    }
    onFlush();
  };
  useLayoutEffect(() => {
    const flush = () => flushRef.current();
    window.addEventListener('pagehide', flush);
    return () => { flush(); window.removeEventListener('pagehide', flush); };
  }, []);

  // While a reveal-to-target is owed, keep asking the host until the range covers the row.
  const driveReveal = useCallback((messageId: string): void => {
    const index = indices.get(messageId);
    if (index !== undefined && index < logicalStart) onRevealTo(index);
  }, [indices, logicalStart, onRevealTo]);

  const position = useCallback((messageId: string, offset: number, navigate?: TranscriptNavigate): void => {
    if (!indices.has(messageId)) return;
    virtualizer.cancelScroll();
    pending.current = {messageId, offset, resolveOffset: navigate?.resolveOffset, startedAt: performance.now(), settled: 0};
    setTargeting(true);
    driveReveal(messageId);
    schedule();
  }, [driveReveal, indices, schedule, virtualizer]);

  const revealTo = useCallback((fullIndex: number, anchor?: ReadingAnchor | null, navigate?: TranscriptNavigate): void => {
    if (anchor) position(anchor.messageId, anchor.offset, navigate);
    if (fullIndex < logicalStart) onRevealTo(fullIndex);
    schedule();
  }, [logicalStart, onRevealTo, position, schedule]);

  const scrollToLatest = useCallback((navigate?: TranscriptNavigate): void => {
    finishNavigation();
    virtualizer.scrollToEnd({behavior: navigate?.behavior ?? 'auto'});
    schedule();
  }, [finishNavigation, schedule, virtualizer]);
  const scrollToOffset = useCallback((offset: number): void => {
    finishNavigation();
    virtualizer.cancelScroll();
    virtualizer.scrollToOffset(Math.max(0, offset), {align: 'start', behavior: 'auto'});
    schedule();
  }, [finishNavigation, schedule, virtualizer]);

  const invalidateLayout = useRef(() => {});
  invalidateLayout.current = () => {
      const scroller = resolveScrollElement();
      if (!scroller) return;
      const row = rowAtOffset(scroller.scrollTop);
      const target = pending.current;
      const anchor = target ? {messageId: target.messageId, offset: target.offset} : readingAnchor.current ?? (row ? {messageId: row.id, offset: row.top - scroller.scrollTop} : null);
      const margin = measureMargin();
      marginRef.current = margin;
      setScrollMargin(margin);
      virtualizer.measure();
      if (anchor && !atEnd.current) position(anchor.messageId, anchor.offset, {resolveOffset: target?.resolveOffset});
      else schedule();
  };
  useLayoutEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    let width = node.clientWidth;
    const invalidate = () => invalidateLayout.current();
    const observer = new ResizeObserver(entries => {
      const next = entries[0]?.contentRect.width ?? width;
      if (Math.abs(next - width) < 0.5) return;
      width = next;
      invalidate();
    });
    observer.observe(node);
    document.fonts?.addEventListener('loadingdone', invalidate);
    return () => { observer.disconnect(); document.fonts?.removeEventListener('loadingdone', invalidate); };
  }, []);

  useImperativeHandle(ref, () => ({
    rowAtOffset,
    subscribe,
    element: () => containerRef.current,
    position,
    revealTo,
    scrollToLatest,
    scrollToOffset,
  }), [position, rowAtOffset, subscribe, revealTo, scrollToLatest, scrollToOffset]);

  // Measurements are scroller coordinates; the container paints them back relative to its own top
  // edge, so `scrollMargin` is subtracted here and the DOM never shifts when the margin changes.
  const renderRows = (() => {
    const rows = new Map<number, number>(); // local index -> painted top
    for (const item of virtualizer.getVirtualItems()) rows.set(item.index, item.start - scrollMargin);
    if (pinnedIds?.size) {
      const measurements = virtualizer.measurementsCache;
      for (const id of pinnedIds) {
        const fullIndex = indices.get(id);
        const index = fullIndex === undefined ? -1 : fullIndex - logicalStart;
        if (index >= 0 && index < measurements.length && !rows.has(index)) rows.set(index, measurements[index].start - scrollMargin);
      }
    }
    return [...rows.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([index, top]) => ({
        key: ids[logicalStart + index] ?? String(index),
        index,
        fullIndex: logicalStart + index,
        top,
        measure: measureRef,
      }));
  })();
  // `role="list"` plus the rows' `aria-posinset`/`aria-setsize` are what an assistive reader gets for
  // free: the mount is a slice, so the position and the total have to come from the logical range, not
  // from how many list items happen to be in the DOM.
  return <div ref={containerRef} role="list" className="transcript-virtual" style={{position: 'relative', height: virtualizer.getTotalSize(), width: '100%'}}>{children(renderRows)}</div>;
});
