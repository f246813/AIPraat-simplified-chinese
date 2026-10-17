/**
 * The in-conversation search bar and its highlighting.
 *
 * The searching itself is in `search.ts`; this module owns the query, the hit list, the
 * Custom Highlight registration, and the alignment that puts the current hit where the eye
 * already is (about a third down the viewport).
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from 'react';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import {
  alignScrollTop, matchSummary, searchRanges, sourceHasMatch, stepMatch, supportsHighlightApi,
  SEARCH_ACTIVE_HIGHLIGHT, SEARCH_HIGHLIGHT,
} from './search';

export function ChatSearch({content, viewport, hidden, hiddenCount, onRevealEarlier, onAlign, onClose}: {
  content: RefObject<HTMLDivElement | null>; viewport: RefObject<HTMLDivElement | null>;
  hidden: {content: string}[]; hiddenCount: number; onRevealEarlier: () => void;
  /** Tells the transcript that this scroll was ours: aligning to a hit must not count as the reader reaching the top. */
  onAlign: (top: number) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [count, setCount] = useState(0);
  const [active, setActive] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [highlighted] = useState(supportsHighlightApi);
  const ranges = useRef<Range[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { input.current?.focus(); }, []);
  // Streaming text and a growing window both change what is searchable, so re-scan on mutation.
  useEffect(() => {
    const node = content.current;
    if (!node || typeof MutationObserver === 'undefined') return;
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; setGeneration(current => current + 1); });
    });
    observer.observe(node, {childList: true, subtree: true, characterData: true});
    return () => { observer.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, [content]);

  useLayoutEffect(() => {
    const node = content.current;
    ranges.current = node ? searchRanges(node, query) : [];
    setCount(ranges.current.length);
    setActive(current => ranges.current.length ? Math.min(current, ranges.current.length - 1) : 0);
    if (!highlighted) return;
    // Painting through CSS.highlights is what keeps React's DOM untouched; nothing is wrapped or cloned.
    if (ranges.current.length) {
      CSS.highlights.set(SEARCH_HIGHLIGHT, new Highlight(...ranges.current));
    } else {
      CSS.highlights.delete(SEARCH_HIGHLIGHT);
      CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
    }
    return () => {
      // Only this panel's own highlights are removed; another surface's names are untouched.
      CSS.highlights.delete(SEARCH_HIGHLIGHT);
      CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
    };
  }, [content, generation, highlighted, query]);

  useLayoutEffect(()=>{
    if(!highlighted)return;
    const hit=ranges.current[Math.min(active,ranges.current.length-1)];
    if(hit)CSS.highlights.set(SEARCH_ACTIVE_HIGHLIGHT,new Highlight(hit));
    else CSS.highlights.delete(SEARCH_ACTIVE_HIGHLIGHT);
  },[active,count,generation,highlighted,query]);

  const align = useCallback((range: Range | undefined) => {
    const node = viewport.current;
    if (!node || !range) return;
    // A match can live inside the process disclosure and a nested tool record.
    // Reveal its ancestors before measuring the range, otherwise it has no box.
    let parent = range.startContainer.parentElement;
    while (parent && node.contains(parent)) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
      parent = parent.parentElement;
    }
    const rect = range.getBoundingClientRect();
    const top = alignScrollTop({targetTop: rect.top, viewportTop: node.getBoundingClientRect().top, scrollTop: node.scrollTop, clientHeight: node.clientHeight});
    onAlign(top);
    node.scrollTo({top, behavior: 'auto'});
  }, [onAlign, viewport]);

  const go = (delta: number) => {
    if (!count) return;
    const next = stepMatch(active, delta, count);
    setActive(next);
    align(ranges.current[next]);
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    // PI-Desktop SearchDialog.tsx: leave candidate selection/commit to the IME.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    go(event.shiftKey ? -1 : 1);
  };

  const hint = !count && Boolean(query.trim()) && hiddenCount > 0 && sourceHasMatch(hidden, query);
  return <div className="chat-search" role="search">
    <Search size={15} aria-hidden/>
    <input ref={input} className="chat-search-input" type="text" role="searchbox" aria-label="在会话中查找" placeholder="在会话中查找…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={onKeyDown} spellCheck={false} autoCorrect="off" autoCapitalize="off"/>
    <span className="chat-search-count" role="status" aria-live="polite">{query.trim() ? matchSummary(active, count) : ''}</span>
    {!highlighted && <span className="chat-search-warning">此宿主不支持文本高亮，仅能定位到消息</span>}
    <button type="button" aria-label="上一个匹配" title="上一个匹配（Shift + Enter）" disabled={!count} onClick={() => go(-1)}><ChevronUp size={16}/></button>
    <button type="button" aria-label="下一个匹配" title="下一个匹配（Enter）" disabled={!count} onClick={() => go(1)}><ChevronDown size={16}/></button>
    <button type="button" aria-label="关闭查找" title="关闭（Esc）" onClick={onClose}><X size={16}/></button>
    {hint && <p className="chat-search-hint">更早的消息中可能有匹配，但那些行尚未渲染。<button type="button" onClick={onRevealEarlier}>显示更早的 {hiddenCount} 条消息</button></p>}
  </div>;
}
