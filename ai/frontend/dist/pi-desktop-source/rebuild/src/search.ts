/**
 * In-conversation search and hit highlighting.
 *
 * Highlight interaction ported from PI-Desktop (`components/SearchDialog.tsx`,
 * `lib/transcript-search-highlight.ts`, `hooks/use-transcript-search-focus.ts`, fixed revision
 * `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0), with two deliberate divergences:
 *
 * PI searches in the **host** (SQLite, one row per hit) and then re-matches inside the DOM to
 * paint that one message. Sidebar history now uses the Codex host search RPC; this panel's
 * searchable set remains the **rendered transcript**: every hit that is counted is a hit that can
 * be highlighted and aligned to. Matches that exist only in Markdown source are not counted —
 * they become candidates the reader can jump to, labelled as possible matches.
 *
 * Scanning is per message row, not per transcript. Under virtualisation the DOM holds a viewport
 * slice that changes as the reader scrolls, so one row's visible text plus its hit offsets form
 * the cache unit: an unchanged row is reused with no scan at all, a remounted row rebuilds its
 * ranges from the saved offsets, and only a row whose text really changed is matched again.
 * The rendered body is never rescanned as a whole.
 *
 * The highlight itself is the CSS Custom Highlight API, exactly as PI uses it, because it
 * paints text without rewriting the DOM that React owns.
 * Literal matching comes from Codex LiteralMatcher, translated to UTF-16 DOM offsets.
 */
import {literalMatchRanges} from './codex/literal-matcher';

/** A hit inside one message row, as offsets into that row's visible text, so it survives a remount. */
export type TextHit = {start: number; end: number};

/** Chrome that is not message prose: chrome labels, code/mermaid copy buttons, hidden decorations. */
export const SEARCH_NON_CONTENT = 'button, textarea, input, select, [aria-hidden="true"], .minimap-rail, .history-reveal';

type Segment = {node: Text; start: number; end: number};

/** Visible text nodes in document order, skipping non-content chrome. */
export function searchSegments(root: HTMLElement): Segment[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      const value = node.nodeValue;
      if (!value || !value.trim()) return NodeFilter.FILTER_REJECT;
      const parent = (node as Text).parentElement;
      if (!parent || parent.closest(SEARCH_NON_CONTENT)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const segments: Segment[] = [];
  let at = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    segments.push({node: text, start: at, end: at + (text.nodeValue?.length ?? 0)});
    at += text.nodeValue?.length ?? 0;
  }
  return segments;
}

/**
 * The range covering `[start, end)` of a subtree's visible text, or null when those offsets no
 * longer exist. Segments come from `searchSegments`, so offsets and nodes share one coordinate space.
 */
export function rangeForOffsets(segments: Segment[], start: number, end: number): Range | null {
  const first = segments.findIndex(segment => segment.end > start);
  if (first === -1) return null;
  let last = first;
  while (segments[last].end < end && last + 1 < segments.length) last++;
  if (segments[last].end < end) return null;
  const range = document.createRange();
  range.setStart(segments[first].node, start - segments[first].start);
  range.setEnd(segments[last].node, end - segments[last].start);
  return range;
}

/**
 * Match one subtree once: its visible text, the literal hits inside it as offsets, and the ranges
 * for those hits. The text is what a later pass compares against to decide whether this row changed.
 * Codex LiteralMatcher supplies the offsets, including expanded lowercase Unicode.
 */
export function scanText(root: HTMLElement, query: string, limit = 500): {text: string; hits: TextHit[]; ranges: Range[]} {
  const needle = query.trim();
  if (!needle) return {text: '', hits: [], ranges: []};
  const segments = searchSegments(root);
  const text = segments.map(segment => segment.node.nodeValue ?? '').join('');
  const hits: TextHit[] = [];
  const ranges: Range[] = [];
  for (const [start, end] of literalMatchRanges(text, needle, limit)) {
    const range = rangeForOffsets(segments, start, end);
    if (!range) continue;
    hits.push({start, end});
    ranges.push(range);
  }
  return {text, hits, ranges};
}

/** Ranges for saved offsets, rebuilt after the row's text nodes were replaced by a remount. */
export function rangesForHits(root: HTMLElement, hits: TextHit[]): Range[] {
  const segments = searchSegments(root);
  const ranges: Range[] = [];
  for (const hit of hits) {
    const range = rangeForOffsets(segments, hit.start, hit.end);
    if (range) ranges.push(range);
  }
  return ranges;
}

/** Which message row a hit belongs to, so the UI can name it and reach it. */
export function messageIdOf(node: Node | null, root: HTMLElement): string | null {
  const element = node instanceof Element ? node : node?.parentElement ?? null;
  const row = element?.closest<HTMLElement>('[data-message-id]') ?? null;
  return row && root.contains(row) ? row.dataset.messageId ?? null : null;
}

/** Wrapping match navigation, so Enter past the last hit returns to the first. */
export const stepMatch = (current: number, delta: number, total: number): number =>
  total <= 0 ? 0 : ((current + delta) % total + total) % total;

export const matchSummary = (active: number, total: number): string => (total ? `${active + 1}/${total}` : '无匹配');

/**
 * Whether a lowercased literal query appears in the source of any of these messages, with the
 * index of the first such message. Used only to offer a candidate jump — never to claim a count,
 * because Markdown syntax, URLs, chart definitions and tool fields can match without being rendered.
 */
export function sourceCandidates(messages: {id?: string; content: string}[], query: string, limit = 3, excluded: ReadonlySet<string> = new Set()): number[] {
  const needle = query.trim().toLocaleLowerCase();
  const found: number[] = [];
  if (!needle) return found;
  for (let index = 0; index < messages.length && found.length < limit; index++) {
    if (messages[index].id && excluded.has(messages[index].id!)) continue;
    if (messages[index].content.toLocaleLowerCase().includes(needle)) found.push(index);
  }
  return found;
}

export const supportsHighlightApi = (): boolean =>
  typeof CSS !== 'undefined' && 'highlights' in CSS && typeof (globalThis as {Highlight?: unknown}).Highlight === 'function';

export const SEARCH_HIGHLIGHT = 'transcript-search';
export const SEARCH_ACTIVE_HIGHLIGHT = 'transcript-search-active';
