/**
 * The in-conversation search bar and its highlighting.
 *
 * The matching itself is in `search.ts`; this module owns the query, the hit list, the Custom
 * Highlight registration, and the jump that puts a hit in view.
 *
 * Only rendered text is counted. Under virtualisation the rendered text is the mounted slice, so
 * the count means "matches in the area currently on screen" and each hit is one that can be
 * highlighted and aligned to. Rows are scanned once each and reused while their text is unchanged;
 * a row that leaves and returns gets its ranges rebuilt from its saved offsets, so the active hit
 * survives recycling by message id and in-row position rather than by a stale Range.
 *
 * A jump goes through the transcript's single positioning entry: it opens the logical range if the
 * target sits above it, mounts the row, and lands once the row measures. Source-only matches are
 * offered as candidates labelled as possible matches, never as counted hits, and a candidate that
 * turns out to have no rendered match is reported as a miss instead of being kept in the list.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import {
  matchSummary, rangesForHits, scanText, sourceCandidates, stepMatch, supportsHighlightApi,
  SEARCH_ACTIVE_HIGHLIGHT, SEARCH_HIGHLIGHT, type TextHit,
} from './search';

/** How many hits one row contributes before its own matches stop being collected. */
const ROW_HIT_LIMIT = 200;
/** How far the bar looks for an already-visible hit when a new query first produces matches. */
const ACTIVE_PROBE_LIMIT = 24;

type RowEntry = {text: string; version: string; hits: TextHit[]; ranges: Range[]};
/** A counted hit: which row it lives in, its position among that row's matches, and its live range. */
type LocatedHit = {messageId: string; hit: number; range: Range};

export function ChatSearch({getContainer, getViewport, messages, logicalStart, onLocate, onClose}: {
  getContainer: () => HTMLElement | null;
  /** The scroll viewport: what "a match the reader can already see" is measured against. */
  getViewport: () => HTMLElement | null;
  /** The loaded history: source scanning here only yields candidates, never a counted hit. */
  messages: {id: string; content: string}[];
  /** Index where the expanded logical range starts; rows above it are not scrollable yet. */
  logicalStart: number;
  /** Jump to a hit or candidate: its row is mounted, then positioned through the unified entry. */
  onLocate: (messageId: string, resolveOffset?: () => number | null) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [count, setCount] = useState(0);
  const [active, setActive] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [mountedIds, setMountedIds] = useState<string[]>([]);
  const [miss, setMiss] = useState<string | null>(null);
  const [highlighted] = useState(supportsHighlightApi);
  const rows = useRef(new Map<string, RowEntry>());
  const list = useRef<LocatedHit[]>([]);
  const lastQuery = useRef('');
  // The active hit is a message id plus its position among that row's matches, so recycling the row
  // and mounting it again restores the same hit instead of the row that happens to sit nearby.
  const activeKey = useRef<{messageId: string; hit: number} | null>(null);
  const pendingCandidate = useRef<string | null>(null);
  const mountedKey = useRef('');
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { input.current?.focus(); }, []);
  // Streaming text, recycling and a growing range all change what is rendered and searchable.
  useEffect(() => {
    const node = getContainer();
    if (!node || typeof MutationObserver === 'undefined') return;
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; setGeneration(current => current + 1); });
    });
    observer.observe(node, {childList: true, subtree: true, characterData: true});
    return () => { observer.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, [getContainer]);

  useLayoutEffect(() => {
    const node = getContainer();
    const needle = query.trim();
    if (query !== lastQuery.current) {
      rows.current.clear();
      activeKey.current = null;
      pendingCandidate.current = null;
      lastQuery.current = query;
      setMiss(null);
    }
    const mounted = node && needle ? Array.from(node.querySelectorAll<HTMLElement>('[data-message-id]')) : [];
    const found: LocatedHit[] = [];
    const seen = new Set<string>();
    for (const row of mounted) {
      const id = row.dataset.messageId;
      if (!id) continue;
      seen.add(id);
      // `textContent` is a cheap presence-of-change signal: it covers chrome too, so it can ask for
      // an unnecessary rescan but never skips a real text change, and it walks no nodes.
      const version = row.textContent ?? '';
      const cached = rows.current.get(id);
      let entry: RowEntry;
      if (!cached || cached.version !== version) entry = {...scanText(row, needle, ROW_HIT_LIMIT), version};
      else if (cached.ranges.every(range => row.contains(range.startContainer) && row.contains(range.endContainer))) entry = cached;
      else entry = {...cached, ranges: rangesForHits(row, cached.hits)};
      rows.current.set(id, entry);
      entry.ranges.forEach((range, hit) => found.push({messageId: id, hit, range}));
    }
    // An unmounted row keeps no Range and no cache entry: its DOM is gone and its hits are not counted.
    for (const id of [...rows.current.keys()]) if (!seen.has(id)) rows.current.delete(id);
    list.current = found;
    setCount(found.length);
    const ids = mounted.map(row => row.dataset.messageId ?? '');
    const idsKey = ids.join('|');
    if (idsKey !== mountedKey.current) { mountedKey.current = idsKey; setMountedIds(ids); }

    // Restore the tracked hit when its row is among the matches, otherwise settle on the first
    // already-visible one so entering a query never yanks the reading position elsewhere.
    if (!found.length) setActive(0);
    else {
      const tracked = activeKey.current ? found.findIndex(entry => entry.messageId === activeKey.current!.messageId && entry.hit === activeKey.current!.hit) : -1;
      const viewport = getViewport();
      const next = tracked >= 0 ? tracked : viewport ? firstInView(viewport, found) : 0;
      activeKey.current = {messageId: found[next].messageId, hit: found[next].hit};
      setActive(current => current === next ? current : next);
    }

    if (!highlighted) return;
    if (found.length) CSS.highlights.set(SEARCH_HIGHLIGHT, new Highlight(...found.map(entry => entry.range)));
    else {
      CSS.highlights.delete(SEARCH_HIGHLIGHT);
      CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
    }
    return () => {
      // Only this panel's own highlights are removed; another surface's names are untouched.
      CSS.highlights.delete(SEARCH_HIGHLIGHT);
      CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
    };
  }, [getContainer, getViewport, generation, highlighted, query]);

  useLayoutEffect(() => {
    if (!highlighted) return;
    const hit = list.current[Math.min(active, list.current.length - 1)];
    if (hit) CSS.highlights.set(SEARCH_ACTIVE_HIGHLIGHT, new Highlight(hit.range));
    else CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
  }, [active, count, generation, highlighted, query]);

  // Open any disclosure holding the hit, then route its row to the single positioning entry.
  const locate = useCallback((entry: LocatedHit | undefined) => {
    const node = getContainer();
    if (!node || !entry || !node.contains(entry.range.startContainer)) return;
    let parent = entry.range.startContainer.parentElement;
    while (parent && node.contains(parent)) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
      parent = parent.parentElement;
    }
    const id = entry.messageId;
    const index = entry.hit;
    onLocate(id, () => {
      const current = rows.current.get(id)?.ranges[index];
      if (!current || !current.startContainer.isConnected) return null;
      const row = current.startContainer.parentElement?.closest<HTMLElement>('[data-message-id]');
      return row ? current.getBoundingClientRect().top - row.getBoundingClientRect().top : null;
    });
  }, [getContainer, onLocate]);

  // Candidate counts come from rendered text, and a deep match is aligned inside its message.
  useLayoutEffect(() => {
    const id = pendingCandidate.current;
    if (!id) return;
    const entry = rows.current.get(id);
    if (!entry) return;
    pendingCandidate.current = null;
    const hit = list.current.findIndex(item => item.messageId === id);
    setMiss(hit >= 0 ? null : '该候选在当前显示内容里没有匹配');
    if (hit >= 0) {
      activeKey.current = {messageId: id, hit: list.current[hit].hit};
      setActive(hit);
      locate(list.current[hit]);
    }
  }, [generation, query, locate]);

  const go = (delta: number) => {
    if (!count) return;
    const next = stepMatch(active, delta, count);
    const entry = list.current[next];
    activeKey.current = entry ? {messageId: entry.messageId, hit: entry.hit} : null;
    setActive(next);
    locate(entry);
  };

  const jumpToCandidate = (id: string) => {
    pendingCandidate.current = id;
    onLocate(id);
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    // PI-Desktop SearchDialog.tsx: leave candidate selection/commit to the IME.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    go(event.shiftKey ? -1 : 1);
  };

  // Source scanning is a candidate finder over the loaded history, so it runs when the query, the
  // mount or the expanded range changes — not on every keystroke-driven re-render of the bar.
  const candidates = useMemo(() => {
    if (!query.trim()) return [];
    const onScreen = new Set(mountedIds);
    return sourceCandidates(messages, query, 3, onScreen);
  }, [messages, mountedIds, query]);
  return <div className="chat-search" role="search">
    <Search size={15} aria-hidden/>
    <input ref={input} className="chat-search-input" type="text" role="searchbox" aria-label="在会话中查找" placeholder="在会话中查找…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={onKeyDown} spellCheck={false} autoCorrect="off" autoCapitalize="off"/>
    <span className="chat-search-count" role="status" aria-live="polite" title="当前显示区域的匹配">{query.trim() ? matchSummary(active, count) : ''}</span>
    {!highlighted && <span className="chat-search-warning">此宿主不支持文本高亮，仅能定位到消息</span>}
    <button type="button" aria-label="上一个匹配" title="上一个匹配（Shift + Enter）" disabled={!count} onClick={() => go(-1)}><ChevronUp size={16}/></button>
    <button type="button" aria-label="下一个匹配" title="下一个匹配（Enter）" disabled={!count} onClick={() => go(1)}><ChevronDown size={16}/></button>
    <button type="button" aria-label="关闭查找" title="关闭（Esc）" onClick={onClose}><X size={16}/></button>
    {miss && <p className="chat-search-miss" role="status">{miss}</p>}
    {candidates.length > 0 && <p className="chat-search-hint">
      <span>以下消息的正文可能匹配，展开后按实际显示内容计数</span>
      {candidates.map(index => <button type="button" key={messages[index].id} onClick={() => jumpToCandidate(messages[index].id)}>
        {index < logicalStart ? `展开并跳转 第 ${index + 1} 条` : `跳转 第 ${index + 1} 条`}
      </button>)}
    </p>}
  </div>;
}

/** The first match fully inside the viewport, else the one nearest to it. */
function firstInView(viewportElement: HTMLElement, found: LocatedHit[]): number {
  const viewport = viewportElement.getBoundingClientRect();
  let best = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < found.length && index < ACTIVE_PROBE_LIMIT; index++) {
    const rect = found[index].range.getBoundingClientRect();
    if (rect.top >= viewport.top && rect.bottom <= viewport.bottom) return index;
    const distance = rect.top >= viewport.bottom ? rect.top - viewport.bottom : viewport.top - rect.bottom;
    if (distance < bestDistance) { bestDistance = distance; best = index; }
  }
  return best;
}
