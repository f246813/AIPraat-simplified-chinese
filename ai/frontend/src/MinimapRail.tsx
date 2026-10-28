/**
 * Conversation minimap rail. Wiring only: the vocabulary, thresholds, track capacity and
 * grouping live in `minimap.ts`, and every row position comes from the virtual engine's
 * measurement metadata (`getRow`) rather than a scan of mounted DOM — so the rail describes
 * the whole expanded logical history, including rows that are currently outside the mount window.
 *
 * The rail never renders more dashes than the track holds. A short history keeps one dash per
 * message; a long one renders contiguous groups whose tooltip announces the message range, and
 * activating a group opens a bounded list of the messages inside it that navigates to an
 * explicit ID through the transcript's unified positioning entry (`onJump`), which opens the
 * logical range and lands the row even when it was not mounted.
 *
 * Ordinary scrolling only answers "which message is the reading line on" and repaints the
 * previous plus the current dash, a pointer move only restyles the dashes within the magnify
 * radius, and both write through cached DOM nodes instead of re-rendering React per frame.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from 'react';
import {
  groupIndexForMarker, groupMarkers, groupPanelHeight, groupRangeLabel, groupWindow,
  magnifyScale, markerGap, MINIMAP_ANCHOR_RATIO, MINIMAP_DASH_PX, MINIMAP_GROUP_ROW_PX, MINIMAP_JUMP_OFFSET_PX,
  MINIMAP_MAGNIFY_RADIUS, MINIMAP_POPOVER_SNAP, MINIMAP_TRACK_INSET_PX, OVERFLOW_EPSILON_PX, popoverTop,
  prefersReducedMotion, shouldRenderMinimap, trackCapacity, type ConversationMarker, type MarkerGroup,
} from './minimap';
import type { RowOffset } from './transcript-window';

const roleLabel = (role: 'user' | 'assistant'): string => role === 'user' ? '你' : '声学助手';
/** An in-group option: where the message sits in the session, then who said what. */
const itemLabel = (marker: ConversationMarker, position: number): string => `第 ${position} 条 · ${roleLabel(marker.role)}：${marker.preview.replace(/\s+/g, ' ').slice(0, 32)}`;

export function ConversationMinimap({markers, viewport, getRow, subscribe, hiddenAbove, indexOffset = 0, onRevealEarlier, onJump}: {
  markers: ConversationMarker[]; viewport: RefObject<HTMLDivElement | null>; getRow: (offset: number) => RowOffset | null;
  subscribe: (listener: () => void) => () => void;
  hiddenAbove: number; /** Where the expanded range starts in the session, so ranges are numbered from the first message. */ indexOffset?: number; onRevealEarlier: () => void; onJump: (id: string, offset: number, behavior: 'auto' | 'smooth') => void;
}) {
  const rail = useRef<HTMLElement>(null);
  const rangeLabel = useCallback((group: MarkerGroup): string => groupRangeLabel(group, indexOffset), [indexOffset]);
  /** Dash centers in track coordinates plus their buttons, in rail order; rebuilt on regroup. */
  const dashes = useRef<{y: number; button: HTMLButtonElement}[]>([]);
  const styled = useRef({lo: 0, hi: -1});
  /** The reading position as marker and dash indices; `-1` before the first batch. */
  const reading = useRef({marker: -1, group: -1});
  const painted = useRef(-1);
  const [visible, setVisible] = useState(false);
  const [trackHeight, setTrackHeight] = useState(0);
  const [hovered, setHovered] = useState<{group: number; top: number} | null>(null);
  // An open group is remembered by the message the reader was on, never by a dash index:
  // a later regrouping may move the window but must not silently change the target.
  const [open, setOpen] = useState<{messageId: string} | null>(null);

  // The track is measured before the rail first paints (the layout effect below sets both this
  // and `visible` in one batch), so a group split always uses the real height: even a collapsed
  // track gets one dash rather than a rail that overflows it.
  const capacity = trackCapacity(Math.max(MINIMAP_DASH_PX, trackHeight), hiddenAbove > 0 ? 1 : 0);
  const groups = useMemo(() => groupMarkers(markers, capacity), [markers, capacity]);
  const markerIndexByRow = useMemo(() => new Map(markers.map((marker, index) => [marker.id, index])), [markers]);
  const openIndex = open ? groups.findIndex(group => group.markers.some(marker => marker.id === open.messageId)) : -1;
  const openGroup = openIndex === -1 ? undefined : groups[openIndex];
  const openItems = openGroup ? groupWindow(openGroup.markers, open!.messageId) : [];
  const openFrom = openGroup && openItems.length ? openGroup.markers.findIndex(marker => marker.id === openItems[0].id) : 0;
  const moveGroupWindow = (direction: -1 | 1) => {
    if (!openGroup || !openItems.length) return;
    const center = openFrom + Math.floor((openItems.length - 1) / 2);
    const index = Math.max(0, Math.min(openGroup.markers.length - 1, center + direction * openItems.length));
    setOpen({messageId: openGroup.markers[index].id});
  };

  const paintActive = useCallback(() => {
    const next = reading.current.group;
    if (next === painted.current) return;
    const previous = dashes.current[painted.current];
    if (previous) { previous.button.classList.remove('active'); previous.button.removeAttribute('aria-current'); }
    const current = dashes.current[next];
    if (current) { current.button.classList.add('active'); current.button.setAttribute('aria-current', 'true'); }
    // An index whose dash is not in the rail yet stays unpainted until the regroup repaints.
    painted.current = current ? next : -1;
  }, []);

  const measureDashes = useCallback(() => {
    const node = rail.current;
    dashes.current = Array.from(node?.querySelectorAll<HTMLButtonElement>('.minimap-marker:not(.history)') || [])
      .map(button => ({y: button.offsetTop + button.offsetHeight / 2, button}));
    styled.current = {lo: 0, hi: -1};
    painted.current = -1;
    paintActive();
  }, [paintActive]);

  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const sync = () => {
      setVisible(shouldRenderMinimap({markerCount: markers.length, overflows: node.scrollHeight - node.clientHeight > OVERFLOW_EPSILON_PX, hasEarlier: hiddenAbove > 0}));
      setTrackHeight(Math.max(0, node.clientHeight - MINIMAP_TRACK_INSET_PX * 2));
      const row = getRow(node.scrollTop + node.clientHeight * MINIMAP_ANCHOR_RATIO);
      const marker = row ? markerIndexByRow.get(row.id) ?? reading.current.marker : 0;
      reading.current = {marker, group: groupIndexForMarker(groups, marker)};
      paintActive();
    };
    sync();
    return subscribe(sync);
  }, [getRow, subscribe, groups, hiddenAbove, markerIndexByRow, markers.length, paintActive, viewport]);

  useLayoutEffect(() => { measureDashes(); }, [measureDashes, groups, hiddenAbove, visible, trackHeight]);

  /** The dash nearest to `y`, found by walking the rail's ordered centers rather than every dash. */
  const nearestTo = (y: number): {index: number; distance: number} => {
    const entries = dashes.current;
    if (!entries.length) return {index: -1, distance: Number.POSITIVE_INFINITY};
    let low = 0; let high = entries.length - 1; let at = entries.length;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (entries[mid].y < y) low = mid + 1; else at = mid, high = mid - 1;
    }
    const before = {index: at - 1, distance: at > 0 ? y - entries[at - 1].y : Number.POSITIVE_INFINITY};
    const after = {index: at, distance: at < entries.length ? entries[at].y - y : Number.POSITIVE_INFINITY};
    return before.distance <= after.distance ? before : after;
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const node = rail.current;
    if (!node) return;
    if (!dashes.current.length) measureDashes();
    const entries = dashes.current;
    const y = event.clientY - node.getBoundingClientRect().top;
    const nearest = nearestTo(y);
    // Only the dashes inside the magnify radius change; the rest keep whatever they had.
    let lo = nearest.index; let hi = nearest.index;
    while (lo > 0 && y - entries[lo - 1].y < MINIMAP_MAGNIFY_RADIUS) lo--;
    while (hi > -1 && hi + 1 < entries.length && entries[hi + 1].y - y < MINIMAP_MAGNIFY_RADIUS) hi++;
    if (nearest.index >= 0) {
      const previous = styled.current;
      for (let index = previous.lo; index <= previous.hi; index++) if (index < lo || index > hi) entries[index]?.button.style.removeProperty('--magnify');
      for (let index = lo; index <= hi; index++) entries[index].button.style.setProperty('--magnify', magnifyScale(entries[index].y - y).toFixed(3));
      styled.current = {lo, hi};
    }
    const hit = nearest.index >= 0 && nearest.distance <= MINIMAP_POPOVER_SNAP ? entries[nearest.index] : null;
    setHovered(current => {
      const next = hit ? {group: nearest.index, top: popoverTop(hit.y, node.clientHeight)} : null;
      return current?.group === next?.group && current?.top === next?.top ? current : next;
    });
  };
  const clearMagnify = () => {
    const entries = dashes.current;
    for (let index = styled.current.lo; index <= styled.current.hi; index++) entries[index]?.button.style.removeProperty('--magnify');
    styled.current = {lo: 0, hi: -1};
    setHovered(null);
  };

  const jump = (id: string) => {
    // A reader action handed to the transcript's single positioning entry: the same inset the
    // rail used becomes the anchor offset, and the engine opens the logical range / refines after
    // measure. It is deliberately not an internal write, so the reading anchor follows.
    onJump(id, MINIMAP_JUMP_OFFSET_PX, prefersReducedMotion() ? 'auto' : 'smooth');
  };

  /**
   * One dash is the message it stands for, so it navigates directly. A group is a range, so it
   * opens its bounded list instead of guessing which message the reader meant.
   */
  const activate = (group: MarkerGroup, index: number) => {
    if (group.markers.length === 1) { setOpen(null); jump(group.key); return; }
    if (openIndex === index) { setOpen(null); return; }
    const readingMarker = reading.current.marker;
    const anchor = readingMarker >= group.from && readingMarker <= group.to ? markers[readingMarker].id : group.markers[0].id;
    setOpen({messageId: anchor});
  };

  useEffect(() => {
    if (!open) return;
    // A click outside the rail dismisses the list; the list itself lives inside the rail,
    // so its own rows stay clickable.
    const onPointerDown = (event: PointerEvent) => { if (!rail.current?.contains(event.target as Node)) setOpen(null); };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    rail.current?.querySelector<HTMLButtonElement>(`[data-group-item="${CSS.escape(open.messageId)}"]`)?.focus();
  }, [open]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape' || !open) return;
    event.stopPropagation();
    setOpen(null);
    dashes.current[openIndex]?.button.focus();
  };

  // Rendering before the first overflow measurement would flash an empty rail.
  if (!visible || (!markers.length && hiddenAbove === 0)) return null;
  const hoveredGroup = hovered && hovered.group < groups.length ? groups[hovered.group] : undefined;
  const hoveredIsGroup = Boolean(hoveredGroup && hoveredGroup.markers.length > 1);
  return <nav ref={rail} className="minimap-rail" aria-label="会话缩略图" style={{'--minimap-gap': `${markerGap(groups.length + (hiddenAbove > 0 ? 1 : 0))}px`} as CSSProperties} onPointerMove={onPointerMove} onPointerLeave={clearMagnify} onKeyDown={onKeyDown}>
    {hiddenAbove > 0 && <button type="button" className="minimap-marker history" aria-label={`显示更早的 ${hiddenAbove} 条消息`} title={`显示更早的 ${hiddenAbove} 条消息`} onClick={onRevealEarlier}/>}
    {groups.map((group, index) => {
      const single = group.markers.length === 1;
      return <button key={group.key} type="button" {...(single ? {'data-marker-id': group.key} : {'data-group-key': group.key, 'data-group-size': group.markers.length, 'data-group-range': rangeLabel(group)})}
        className={`minimap-marker ${group.role}${single ? '' : ' group'}`}
        aria-label={single ? `${group.role === 'user' ? '你说' : '声学助手'}：${group.markers[0].preview.slice(0, 40)}` : `${rangeLabel(group)}，${group.markers.length} 条消息`}
        aria-expanded={single ? undefined : openIndex === index}
        onClick={() => activate(group, index)}
        onFocus={() => setHovered({group: index, top: popoverTop(dashes.current[index]?.y ?? 0, trackHeight)})}
        onBlur={() => setHovered(null)}/>;
    })}
    {hovered && hoveredGroup && <div className="minimap-popover" role="tooltip" style={{top: `${hovered.top}px`}}>
      <div className="minimap-popover-role">{hoveredIsGroup ? `${rangeLabel(hoveredGroup)} · 点击选择消息` : roleLabel(hoveredGroup.role)}</div>
      <div className="minimap-popover-text">{hoveredGroup.markers[0].preview}</div>
    </div>}
    {open && openGroup && <div className="minimap-group-list" role="group" aria-label={`${rangeLabel(openGroup)} 中的消息`} style={{top: `${popoverTop(dashes.current[openIndex]?.y ?? 0, trackHeight, Math.min(groupPanelHeight(openItems.length), trackHeight))}px`, maxHeight: `${Math.max(MINIMAP_GROUP_ROW_PX, Math.min(groupPanelHeight(openItems.length), trackHeight))}px`}}>
      <div className="minimap-group-list-range">{rangeLabel(openGroup)}</div>
      {openFrom > 0 && <button type="button" aria-label="组内上一页消息" onClick={() => moveGroupWindow(-1)}>上一页消息</button>}
      {openItems.map(marker => <button key={marker.id} type="button" data-group-item={marker.id} aria-current={marker.id === open.messageId ? 'true' : undefined} onClick={() => { setOpen(null); jump(marker.id); }}>{itemLabel(marker, (markerIndexByRow.get(marker.id) ?? 0) + indexOffset + 1)}</button>)}
      {openFrom + openItems.length < openGroup.markers.length && <button type="button" aria-label="组内下一页消息" onClick={() => moveGroupWindow(1)}>下一页消息</button>}
    </div>}
  </nav>;
}
