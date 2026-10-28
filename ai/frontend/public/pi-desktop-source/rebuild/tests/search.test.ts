import { afterEach, expect, it } from 'vitest';
import {
  matchSummary, messageIdOf, rangeForOffsets, rangesForHits, scanText, searchSegments, sourceCandidates,
  stepMatch, supportsHighlightApi,
} from '../src/search';

const mount = (html: string) => {
  const root = document.createElement('div');
  root.innerHTML = html;
  document.body.append(root);
  return root;
};
const row = '<div data-message-id="m1"><p>Hello <strong>world</strong> hello</p><button>hello</button></div><div data-message-id="m2" aria-hidden="true">hello</div>';
afterEach(() => { document.body.innerHTML = ''; });

it('reads only message prose, not the chrome around it', () => {
  const root = mount(row);
  expect(searchSegments(root).map(segment => segment.node.nodeValue)).toEqual(['Hello ', 'world', ' hello']);
});

it('matches case-insensitively and across inline elements', () => {
  const root = mount(row);
  expect(scanText(root, 'hello').ranges).toHaveLength(2);
  expect(scanText(root, 'HELLO').ranges).toHaveLength(2);
  expect(scanText(root, 'zzz').ranges).toEqual([]);
  expect(scanText(root, '   ')).toEqual({text: '', hits: [], ranges: []});
  // A hit that spans an inline element still resolves to one range.
  const spanning = scanText(root, 'o w');
  expect(spanning.ranges).toHaveLength(1);
  expect(spanning.ranges[0].toString()).toBe('o w');
  expect(spanning.text).toBe('Hello world hello');
  // Hits never overlap each other: "Hello world hello" holds five separate l's.
  expect(scanText(root, 'l').ranges).toHaveLength(5);
  expect(scanText(root, 'l', 2).ranges).toHaveLength(2);
});

it('maps case-expanded Unicode matches back to the original DOM offsets', () => {
  const root = mount('<p>İ <strong>hello</strong> İ</p>');
  expect(scanText(root, 'i\u0307').ranges.map(range => range.toString())).toEqual(['İ', 'İ']);
});

it('saves a hit as offsets so a remounted row rebuilds the same selection', () => {
  const root = mount('<div data-message-id="m1"><p>alpha beta <em>gamma</em></p></div>');
  const m1 = root.querySelector<HTMLElement>('[data-message-id="m1"]')!;
  const saved = scanText(m1, 'be');
  expect(saved.hits).toEqual([{start: 6, end: 8}]);
  // React replaces the text nodes when the row remounts; only the offsets carry over.
  m1.innerHTML = '<p>alpha <span>be</span>ta gamma</p>';
  const rebuilt = rangesForHits(m1, saved.hits);
  expect(rebuilt.map(range => range.toString())).toEqual(['be']);
});

it('drops a saved offset the current text no longer covers', () => {
  const root = mount('<p>alpha</p>');
  const segments = searchSegments(root);
  expect(rangeForOffsets(segments, 0, 5)?.toString()).toBe('alpha');
  expect(rangeForOffsets(segments, 3, 9)).toBeNull();
  expect(rangeForOffsets([], 0, 2)).toBeNull();
});

it('attributes a hit to the row that owns it', () => {
  const root = mount('<div data-message-id="m1"><p>alpha</p></div><div data-message-id="m2"><p>beta</p></div>');
  const hits = scanText(root, 'beta').ranges;
  expect(messageIdOf(hits[0].startContainer, root)).toBe('m2');
  expect(messageIdOf(root, root)).toBeNull();
  expect(messageIdOf(null, root)).toBeNull();
});

it('wraps match navigation and reports it', () => {
  expect(stepMatch(0, 1, 3)).toBe(1);
  expect(stepMatch(2, 1, 3)).toBe(0);
  expect(stepMatch(0, -1, 3)).toBe(2);
  expect(stepMatch(5, 1, 0)).toBe(0);
  expect(matchSummary(0, 0)).toBe('无匹配');
  expect(matchSummary(1, 5)).toBe('2/5');
});

it('offers source candidates in order and bounded, never a count', () => {
  const messages = [{id: 'a', content: '第 20 条夹具'}, {id: 'b', content: '另一条'}, {id: 'c', content: '夹具 again'}, {id: 'd', content: '夹具 once more'}];
  expect(sourceCandidates(messages, '夹具')).toEqual([0, 2, 3]);
  expect(sourceCandidates(messages, '  第 20  ')).toEqual([0]);
  expect(sourceCandidates(messages, '不存在')).toEqual([]);
  expect(sourceCandidates(messages, '   ')).toEqual([]);
  expect(sourceCandidates(messages, '夹具', 1, new Set(['a']))).toEqual([2]);
  expect(sourceCandidates(messages, '夹具', 2, new Set(['a', 'c']))).toEqual([3]);
});

it('reports the highlight API as unavailable instead of throwing when it is missing', () => {
  // jsdom has no CSS.highlights, which is exactly the branch a host without it would take.
  expect(supportsHighlightApi()).toBe(false);
});
