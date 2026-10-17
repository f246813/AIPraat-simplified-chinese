import { expect, it } from 'vitest';
import {
  anchorFor, growTranscriptWindow, prependedHeight, reduceTranscriptWindow, scrollTopFor, windowForMessage,
  TRANSCRIPT_INITIAL_MOUNT, TRANSCRIPT_WINDOW_MIN, TRANSCRIPT_WINDOW_STEP, type RowOffset,
} from '../src/transcript-window';

it('mounts the tail of the history and reports what it withheld', () => {
  expect(TRANSCRIPT_INITIAL_MOUNT).toBe(15);
  expect(TRANSCRIPT_WINDOW_MIN).toBe(60);
  expect(TRANSCRIPT_WINDOW_STEP).toBe(40);
  // First commit of a session paints only the tail, the steady window is wider.
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 60, initialCommit: true})).toEqual({mounted: 15, hiddenAbove: 106, bounded: true});
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 60, initialCommit: false})).toEqual({mounted: 60, hiddenAbove: 61, bounded: true});
  // A short history is never windowed, so nothing is hidden from the user.
  expect(reduceTranscriptWindow({historyLength: 10, windowSize: 60, initialCommit: false})).toEqual({mounted: 10, hiddenAbove: 0, bounded: false});
  expect(reduceTranscriptWindow({historyLength: 0, windowSize: 60, initialCommit: true})).toEqual({mounted: 0, hiddenAbove: 0, bounded: false});
  // A caller asking for less than the steady minimum still gets a usable window.
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 3, initialCommit: false}).mounted).toBe(60);
});

it('grows by one step and never past the loaded history', () => {
  expect(growTranscriptWindow(60, 121)).toBe(100);
  expect(growTranscriptWindow(100, 121)).toBe(121);
  expect(growTranscriptWindow(121, 121)).toBe(121);
  expect(growTranscriptWindow(60, 70)).toBe(70);
  expect(growTranscriptWindow(0, 121)).toBe(100);
});

it('answers how much must be mounted for a message to be included', () => {
  expect(windowForMessage(['a', 'b', 'c'], 'c')).toBe(1);
  expect(windowForMessage(['a', 'b', 'c'], 'b')).toBe(2);
  expect(windowForMessage(['a', 'b', 'c'], 'a')).toBe(3);
  expect(windowForMessage(['a', 'b', 'c'], 'z')).toBeNull();
  expect(windowForMessage([], 'a')).toBeNull();
});

const rows: RowOffset[] = [{id: 'a', top: 0}, {id: 'b', top: 500}, {id: 'c', top: 1000}];

it('anchors the reading position to the row under the viewport top', () => {
  expect(anchorFor(rows, 600)).toEqual({messageId: 'b', offset: -100});
  expect(anchorFor(rows, 500)).toEqual({messageId: 'b', offset: 0});
  expect(anchorFor(rows, 50)).toEqual({messageId: 'a', offset: -50});
  expect(anchorFor(rows, 5000)).toEqual({messageId: 'c', offset: -4000});
  expect(anchorFor([], 0)).toBeNull();
});

it('resolves an anchor back to a scroll position, and refuses an unmounted row', () => {
  expect(scrollTopFor(rows, {messageId: 'b', offset: -100})).toBe(600);
  expect(scrollTopFor(rows, {messageId: 'c', offset: 0})).toBe(1000);
  expect(scrollTopFor(rows, {messageId: 'a', offset: 200})).toBe(0);
  expect(scrollTopFor(rows, {messageId: 'z', offset: 0})).toBeNull();
  // Round trip: the anchor read at a position resolves back to the same position.
  const anchor = anchorFor(rows, 640)!;
  expect(scrollTopFor(rows, anchor)).toBe(640);
  const fractionalRows = [{id:'a',top:24.25},{id:'b',top:500.75}];
  const fractional = anchorFor(fractionalRows, 640.125)!;
  expect(fractional.offset).toBe(-139.375);
  expect(scrollTopFor(fractionalRows, fractional)).toBe(640.125);
});

it('absorbs exactly the height a wider window prepends, and nothing when it shrinks', () => {
  expect(prependedHeight(1000, 1500)).toBe(500);
  expect(prependedHeight(1500, 1000)).toBe(0);
  expect(prependedHeight(1000, 1000)).toBe(0);
});
