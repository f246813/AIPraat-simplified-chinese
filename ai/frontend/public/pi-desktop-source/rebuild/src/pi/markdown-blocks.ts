// SPDX-License-Identifier: LGPL-3.0
// Adapted from PI-Desktop 9f313c8f855ef6f8311622ad63dd7e6583cef2d7
// (apps/desktop/src/lib/markdown-blocks.ts). Source and adaptation details:
// ../../../third_party/pi-desktop/NOTICE.md
//
// Local adaptation removes PI's render-diagnostics host runtime and shares
// code-node metadata with the renderer; the split rules are unchanged.
// The parse grammar is shared with ReactMarkdown
// through `markdownRemarkPlugins`, so the block lexer and the renderer cannot
// diverge on where a block boundary falls.
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import {remarkCodeFences} from '../markdown-code';

// Share the grammar with ReactMarkdown. A different block lexer can split a
// valid math node at a list, quote, heading, or blank line inside its TeX.
//
// "Share" is literal and rests on deduplication: `react-markdown` depends on
// `remark-parse`/`unified` itself, and the ranges in `package.json` are kept
// wide enough to overlap its own so the installer resolves one copy for both.
// Pinning either exactly is the one way to break that.
export const markdownRemarkPlugins = [remarkGfm, remarkMath, remarkCodeFences];
const parser = unified().use(remarkParse).use(markdownRemarkPlugins);

/** Parse authoritative text only when copying, so unseen code is copied in full. */
export function receivedCodeText(source: string, start: number): string | undefined {
  function find(node: unknown): string | undefined {
    if (!node || typeof node !== 'object') return undefined;
    const value = node as {type?: string; value?: string; position?: {start: {offset?: number}}; children?: unknown[]};
    if (value.type === 'code' && value.position?.start.offset === start) return value.value ? value.value + '\n' : '';
    for (const child of value.children ?? []) {
      const result = find(child);
      if (result !== undefined) return result;
    }
    return undefined;
  }
  return find(parser.parse(source));
}

export type MarkdownBlockSplit = {
  /** Source slices that concatenate back to the input. */
  blocks: string[];
  /**
   * The source declares a link or footnote definition, so its slices cannot
   * be parsed independently of each other. See `advanceMarkdownBlocks`.
   */
  hasDocumentScopedDefinition: boolean;
};

/*
 * A definition resolves across the whole document rather than inside its own
 * container: `> [ref]: /url` in a quote still defines `[ref]` for a later
 * paragraph. The search descends for that reason instead of reading the top
 * level, where such a definition never appears as a child.
 */
function declaresDefinition(node: unknown): boolean {
  if (node === null || typeof node !== 'object') return false;
  const { type, children } = node as { type?: unknown; children?: unknown };
  if (type === 'definition' || type === 'footnoteDefinition') return true;
  return Array.isArray(children) && children.some(declaresDefinition);
}

export function parseMarkdownBlocks(source: string): MarkdownBlockSplit {
  if (!source) return { blocks: [], hasDocumentScopedDefinition: false };
  const root = parser.parse(source);
  const blocks: string[] = [];
  let start = 0;
  for (const node of root.children.slice(1)) {
    const nodeStart = node.position?.start.offset;
    if (nodeStart === undefined) {
      // `remark-parse` positions everything it produces; only a plugin that
      // synthesizes a node can leave the offset out. Unreachable for the
      // parser-level plugins above. An undivided source renders identically
      // and only gives up per-block memoization, so degrading is strictly
      // better than throwing into the transcript's error boundary.
      console.warn('Markdown block has no source offset; rendering undivided');
      return { blocks: [source], hasDocumentScopedDefinition: declaresDefinition(root) };
    }
    // Slice the original bytes, including CRLF and inter-block whitespace.
    // Attaching whitespace to the preceding block keeps source anchors and
    // the streaming tail's start aligned without serializing the AST again.
    //
    // Cut at the line start rather than at `nodeStart`: the grammar is
    // line-based, so a block owns the indentation before its first character
    // while `position.start` points past it. A slice that loses those columns
    // re-parses as a different block. No two top-level blocks begin on the same
    // line, so the span skipped here is whitespace and the slices still
    // concatenate back to the source.
    const end = source.lastIndexOf('\n', nodeStart - 1) + 1;
    blocks.push(source.slice(start, end));
    start = end;
  }
  blocks.push(source.slice(start));
  return { blocks, hasDocumentScopedDefinition: declaresDefinition(root) };
}

export type MarkdownBlockCache = {
  /** Source the cached blocks were produced from. */
  consumed: string;
  blocks: string[];
  /** The cached blocks are the whole source, kept undivided on purpose. */
  singleUnit: boolean;
};

export const emptyMarkdownBlockCache: MarkdownBlockCache = {
  consumed: '',
  blocks: [],
  singleUnit: false,
};

/*
 * Split `source` for rendering, reusing what `cache` already settled.
 *
 * A slice may be handed to its own parser only when it needs no other slice's
 * parse context. Definitions break that: they resolve document-wide, and
 * footnotes additionally carry document-wide numbering, reuse and back-links.
 * A source that declares one therefore stays undivided. The latch is one-way:
 * a block only ever comes into existence through a tail parse, so a definition
 * anywhere in the source was seen by the parse that first produced its block,
 * and `singleUnit` keeps that observation for the rest of the append run.
 * Undivided is always a correct rendering, so a stale latch costs speed, never
 * output. A source that stops extending the cached one is an edit rather than
 * an append, and decides again from a full parse.
 *
 * Streaming keeps the settled prefix and re-parses only the tail. A growing
 * unclosed block can otherwise be parsed from its first byte on every frame, so
 * a tail over `maxTailCodeUnits` keeps the stable prefix and renders the tail
 * raw until it closes or streaming ends.
 */
export function advanceMarkdownBlocks(
  cache: MarkdownBlockCache,
  source: string,
  maxTailCodeUnits = Number.POSITIVE_INFINITY,
): MarkdownBlockCache {
  const appended =
    cache.blocks.length > 0 &&
    source.length >= cache.consumed.length &&
    source.startsWith(cache.consumed);
  // Latched one-way; a stale latch costs speed, not output.
  if (appended && cache.singleUnit) {
    return { consumed: source, blocks: [source], singleUnit: true };
  }
  let stable: string[] = [];
  let tail = source;
  if (appended) {
    stable = cache.blocks.slice(0, -1);
    const lastStart = cache.consumed.length - cache.blocks[cache.blocks.length - 1].length;
    tail = source.slice(lastStart);
  }
  if (tail.length > maxTailCodeUnits) {
    // Keep the text complete; render the oversized tail as plain text instead
    // of parsing an unclosed block from its first byte every frame.
    return { consumed: source, blocks: [...stable, tail], singleUnit: false };
  }
  const split = tail ? parseMarkdownBlocks(tail) : { blocks: [], hasDocumentScopedDefinition: false };
  const singleUnit = split.hasDocumentScopedDefinition;
  const blocks = singleUnit ? [source] : [...stable, ...split.blocks];
  return { consumed: source, blocks, singleUnit };
}
