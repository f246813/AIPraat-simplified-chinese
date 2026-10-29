import {describe, expect, it} from 'vitest';
import {advanceMarkdownBlocks, emptyMarkdownBlockCache, parseMarkdownBlocks} from '../src/pi/markdown-blocks';

const blocksOf = (source: string) => parseMarkdownBlocks(source).blocks;

/** Feed `source` one appended chunk at a time, as streaming does. */
function stream(...chunks: string[]) {
  let cache = emptyMarkdownBlockCache;
  let source = '';
  for (const chunk of chunks) {
    source += chunk;
    cache = advanceMarkdownBlocks(cache, source);
  }
  return cache;
}

// Cases extracted from PI-Desktop markdown-blocks.test.mjs at the fixed revision.
describe('parseMarkdownBlocks shares the ReactMarkdown grammar', () => {
  it('treats math bodies as indivisible blocks, including Markdown-looking lines', () => {
    for (const body of ['- x', '+ x', '* x', '> x', 'x\n=\ny', 'x\n\n+ y']) {
      const math = `$$\n${body}\n$$\n\n`;
      expect(blocksOf(`Before.\n\n${math}After.`)).toEqual(['Before.\n\n', math, 'After.']);
    }
  });

  it('preserves CRLF, leading whitespace and ordinary block boundaries', () => {
    const blocks = [
      '\r\nBefore.\r\n\r\n',
      '$$\r\n- x\r\n\r\n+ y\r\n$$\r\n\r\n',
      '- first\r\n- second\r\n\r\n',
      '> quote\r\n\r\n',
      '```text\r\n$$\r\n- literal\r\n$$\r\n```\r\n\r\n',
      '| a | b |\r\n| --- | --- |\r\n| 1 | 2 |\r\n\r\n',
      'After.',
    ];
    expect(blocksOf(blocks.join(''))).toEqual(blocks);
    expect(blocksOf(' \r\n\r\n')).toEqual([' \r\n\r\n']);
    expect(blocksOf('')).toEqual([]);
  });

  it('keeps an indented block\'s own indentation in its own slice', () => {
    expect(blocksOf('Before.\n\n  - first\n  - second\n\nAfter.')).toEqual([
      'Before.\n\n', '  - first\n  - second\n\n', 'After.',
    ]);
    expect(blocksOf('Before.\n\n   1. one\n   1. two\n')).toEqual(['Before.\n\n', '   1. one\n   1. two\n']);
    expect(blocksOf('Before.\r\n\r\n  ```js\r\n  code\r\n  ```\r\n\r\nAfter.')).toEqual([
      'Before.\r\n\r\n', '  ```js\r\n  code\r\n  ```\r\n\r\n', 'After.',
    ]);
  });

  it('retains an unclosed math fence\'s whole body in the streaming tail', () => {
    for (const tail of ['$$\n- x', '$$\n- x\n\n+ y', '$$\n- x\n\n+ y\n$$']) {
      expect(blocksOf(`Before.\n\n${tail}`)).toEqual(['Before.\n\n', tail]);
    }
  });

  it('slices concatenate back to the input exactly', () => {
    for (const source of [
      'Before.\n\n$$\n- x\n$$\n\n- one\n- two\n\nAfter.',
      '# H\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n```js\ncode\n```\n\n中文 ✅ emoji 😀.',
      'Line one\r\nLine two\r\n\r\nParagraph.\r\n',
    ]) {
      expect(blocksOf(source).join('')).toBe(source);
    }
  });
});

describe('document-scoped definitions collapse the source', () => {
  it('a definition anywhere makes the source one parse context', () => {
    for (const source of [
      'Text[^1].\n[^1]: footnote\n',
      'Text[^1].\n\n[^1]: footnote\n',
      'A[^1] and B[^1] reuse one note.\n\n[^1]: footnote\n',
      '[^1]: footnote\n\nText[^1].\n',
      'See [spec].\n\n[spec]: https://example.com\n',
      '> [spec]: https://example.com\n\nSee [spec].\n',
      '- [spec]: https://example.com\n\nSee [spec].\n',
    ]) {
      expect(parseMarkdownBlocks(source).hasDocumentScopedDefinition, source).toBe(true);
      expect(advanceMarkdownBlocks(emptyMarkdownBlockCache, source).blocks, source).toEqual([source]);
    }
  });

  it('sources without definitions keep splitting into independent blocks', () => {
    for (const source of [
      'Before.\n\n$$\nx\n$$\n\nAfter.',
      'Text[^1] whose note never arrives.\n\nNext paragraph.\n',
      'See [spec] with no target.\n\nNext paragraph.\n',
      '```text\n[^1]: fenced, not a definition\n```\n\nAfter.\n',
    ]) {
      expect(parseMarkdownBlocks(source).hasDocumentScopedDefinition, source).toBe(false);
      expect(advanceMarkdownBlocks(emptyMarkdownBlockCache, source).blocks.length, source).toBeGreaterThan(1);
    }
  });

  it('a definition streamed in after its reference still collapses the prefix and latches', () => {
    const cache = stream('Text[^1].\n', '\nMore prose.\n', '\n[^1]: footnote\n');
    expect(cache.singleUnit).toBe(true);
    expect(cache.blocks).toEqual(['Text[^1].\n\nMore prose.\n\n[^1]: footnote\n']);
    const later = advanceMarkdownBlocks(cache, `${cache.consumed}\nTail.\n`);
    expect(later.blocks).toEqual([later.consumed]);
  });
});

describe('advanceMarkdownBlocks streaming reuse', () => {
  it('splits an appended source exactly like one full parse', () => {
    const source = 'Before.\n\n$$\n- x\n$$\n\n- first\n- second\n\nAfter.';
    for (let cut = 1; cut < source.length; cut++) {
      const cache = stream(source.slice(0, cut), source.slice(cut));
      expect(cache.blocks, `cut at ${cut}`).toEqual(blocksOf(source));
    }
  });

  it('keeps settled prefix blocks while an oversized tail stays raw, then finalizes', () => {
    const first = advanceMarkdownBlocks(emptyMarkdownBlockCache, 'Heading\n\nFirst.', 16);
    const growing = `${first.consumed}\n\nSecond paragraph.`;
    const bounded = advanceMarkdownBlocks(first, growing, 16);
    expect(bounded.blocks).toEqual(['Heading\n\n', 'First.\n\nSecond paragraph.']);
    const finished = advanceMarkdownBlocks(bounded, growing, Number.POSITIVE_INFINITY);
    expect(finished.blocks).toEqual(parseMarkdownBlocks(growing).blocks);
  });

  it('re-decides from scratch when the source stops extending the cached one', () => {
    const withDefinition = stream('See [spec].\n\n[spec]: https://example.com\n');
    expect(withDefinition.singleUnit).toBe(true);
    const replaced = advanceMarkdownBlocks(withDefinition, 'Before.\n\nAfter.\n');
    expect(replaced.singleUnit).toBe(false);
    expect(replaced.blocks).toEqual(['Before.\n\n', 'After.\n']);
  });
});
