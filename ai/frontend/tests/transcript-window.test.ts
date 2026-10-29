import { expect, it } from 'vitest';
import {
  anchorFor, growTranscriptWindow, logicalStartIndex, reduceTranscriptWindow, selectionPinSpan,
  SELECTION_PIN_BUDGET, TRANSCRIPT_INITIAL_MOUNT, TRANSCRIPT_WINDOW_MIN, TRANSCRIPT_WINDOW_STEP, type RowOffset,
} from '../src/transcript-window';

it('describes the expanded logical range and reports what sits above it', () => {
  expect(TRANSCRIPT_INITIAL_MOUNT).toBe(15);
  expect(TRANSCRIPT_WINDOW_MIN).toBe(60);
  expect(TRANSCRIPT_WINDOW_STEP).toBe(40);
  // First commit of a session opens only the tail; the steady logical range is wider. This is
  // the range the reader can scroll through, NOT the rows physically mounted by the viewport.
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 60, initialCommit: true})).toEqual({mounted: 15, hiddenAbove: 106, bounded: true});
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 60, initialCommit: false})).toEqual({mounted: 60, hiddenAbove: 61, bounded: true});
  // A short history is never windowed, so nothing is hidden from the user.
  expect(reduceTranscriptWindow({historyLength: 10, windowSize: 60, initialCommit: false})).toEqual({mounted: 10, hiddenAbove: 0, bounded: false});
  expect(reduceTranscriptWindow({historyLength: 0, windowSize: 60, initialCommit: true})).toEqual({mounted: 0, hiddenAbove: 0, bounded: false});
  // A caller asking for less than the steady minimum still gets a usable range.
  expect(reduceTranscriptWindow({historyLength: 121, windowSize: 3, initialCommit: false}).mounted).toBe(60);
});

it('maps a logical range count back to its first index in the full history', () => {
  expect(logicalStartIndex(121, 60)).toBe(61);
  expect(logicalStartIndex(121, 121)).toBe(0);
  expect(logicalStartIndex(10, 60)).toBe(0); // shorter than the range: nothing withheld
  expect(logicalStartIndex(0, 60)).toBe(0);
});

it('grows by one step and never past the loaded history', () => {
  expect(growTranscriptWindow(60, 121)).toBe(100);
  expect(growTranscriptWindow(100, 121)).toBe(121);
  expect(growTranscriptWindow(121, 121)).toBe(121);
  expect(growTranscriptWindow(60, 70)).toBe(70);
  expect(growTranscriptWindow(0, 121)).toBe(100);
});

const rows: RowOffset[] = [{id: 'a', top: 0}, {id: 'b', top: 500}, {id: 'c', top: 1000}];

it('anchors the reading position to the row under the viewport top', () => {
  expect(anchorFor(rows, 600)).toEqual({messageId: 'b', offset: -100});
  expect(anchorFor(rows, 500)).toEqual({messageId: 'b', offset: 0});
  expect(anchorFor(rows, 50)).toEqual({messageId: 'a', offset: -50});
  expect(anchorFor(rows, 5000)).toEqual({messageId: 'c', offset: -4000});
  expect(anchorFor([], 0)).toBeNull();
  // The stored position resolves back to a scroll target as `行首位置 - offset`, the same
  // arithmetic the engine applies to a measured row: a negative offset means the row already
  // started above the viewport, and the round trip has to survive that.
  expect(anchorFor(rows, 640)).toEqual({messageId: 'b', offset: -140});
  expect(rows.find(row => row.id === anchorFor(rows, 640)!.messageId)!.top - anchorFor(rows, 640)!.offset).toBe(640);
  const fractionalRows = [{id: 'a', top: 24.25}, {id: 'b', top: 500.75}];
  const fractional = anchorFor(fractionalRows, 640.125)!;
  expect(fractional.offset).toBe(-139.375);
  expect(fractionalRows.find(row => row.id === fractional.messageId)!.top - fractional.offset).toBe(640.125);
});

it('holds the rows a selection spans, in either drag direction and up to the budget', () => {
  expect(selectionPinSpan(4, 9)).toEqual([4, 9]);
  // Dragging upward gives the same span as dragging downward.
  expect(selectionPinSpan(9, 4)).toEqual([4, 9]);
  expect(selectionPinSpan(7, 7)).toEqual([7, 7]);
  // A drag longer than the budget stops growing rather than pinning the whole history.
  expect(selectionPinSpan(10, 500)).toEqual([10, 10 + SELECTION_PIN_BUDGET - 1]);
  expect(selectionPinSpan(500, 10)).toEqual([500 - SELECTION_PIN_BUDGET + 1, 500]);
});
