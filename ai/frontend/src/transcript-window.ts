/**
 * Long-history rendering: a mount window over the tail of the loaded history, plus the
 * durable reading position that makes windowing safe.
 *
 * Interaction ported from PI-Desktop (`lib/transcript-window.ts`, `lib/transcript-reading.ts`,
 * fixed revision `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0). It is a **mount
 * window**, not pixel virtualisation: the renderer keeps the history it loaded and only
 * withholds the rows above the window, because `content-visibility` would skip layout while
 * still holding every row in memory (ADR 0120).
 *
 * The reading position is the piece this host could not borrow as-is. PI stores a transcript
 * reading target with message ids; this contract only had `scroll`, a pixel offset that stops
 * meaning anything once the coordinate system is "the mounted rows" instead of "the whole
 * history". So `sessions.view` now also carries an anchor.
 */

export const TRANSCRIPT_INITIAL_MOUNT = 15;
export const TRANSCRIPT_WINDOW_MIN = 60;
export const TRANSCRIPT_WINDOW_STEP = 40;
/** How close to the top counts as "reached it" while revealing older pages. */
export const HISTORY_REVEAL_THRESHOLD_PX = 120;

export type TranscriptWindow = {mounted: number; hiddenAbove: number; bounded: boolean};

export function reduceTranscriptWindow({historyLength, windowSize, initialCommit}: {historyLength: number; windowSize: number; initialCommit: boolean}): TranscriptWindow {
  const length = Math.max(0, Math.round(historyLength));
  const budget = initialCommit ? TRANSCRIPT_INITIAL_MOUNT : Math.max(TRANSCRIPT_WINDOW_MIN, Math.round(windowSize));
  const mounted = Math.min(length, budget);
  return {mounted, hiddenAbove: length - mounted, bounded: length - mounted > 0};
}

export function growTranscriptWindow(windowSize: number, historyLength: number): number {
  const current = Math.max(TRANSCRIPT_WINDOW_MIN, Math.round(windowSize));
  if (current >= historyLength) return current;
  return Math.min(historyLength, current + TRANSCRIPT_WINDOW_STEP);
}

/** How many trailing rows must be mounted for `messageId` to be among them. */
export function windowForMessage(ids: string[], messageId: string): number | null {
  const index = ids.indexOf(messageId);
  return index === -1 ? null : ids.length - index;
}

/** A row's top edge in the content's own coordinate space, so it survives any scroll. */
export type RowOffset = {id: string; top: number};

/**
 * The row the viewport top sits in, plus the viewport top's offset from that row's top.
 * A negative offset means the row starts above the viewport, which is exactly what the
 * disclosure anchor also means by offset, so one convention serves both.
 */
export function anchorFor(rows: RowOffset[], scrollTop: number): {messageId: string; offset: number} | null {
  if (!rows.length) return null;
  let low = 0; let high = rows.length - 1; let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (rows[mid].top <= scrollTop) { found = mid; low = mid + 1; } else high = mid - 1;
  }
  const row = rows[found === -1 ? 0 : found];
  // Preserve sub-pixel offsets: rounding can hide the final fraction of the
  // first visible row when restoring after a font/spacing or mount change.
  return {messageId: row.id, offset: row.top - scrollTop};
}

/** The scrollTop that puts the anchored row back at its stored offset, or null when it is not mounted. */
export function scrollTopFor(rows: RowOffset[], anchor: {messageId: string; offset: number}): number | null {
  const row = rows.find(entry => entry.id === anchor.messageId);
  return row ? Math.max(0, row.top - anchor.offset) : null;
}

/** Growing the window adds rows above the reading position, so the scroller absorbs exactly that much. */
export const prependedHeight = (previousHeight: number, currentHeight: number): number => Math.max(0, currentHeight - previousHeight);
