/**
 * Long-history reading: the durable logical range over the tail of the loaded history, plus
 * the reading anchor that makes moving the physical mount window safe.
 *
 * Interaction ported from PI-Desktop (`lib/transcript-window.ts`, `lib/transcript-reading.ts`,
 * fixed revision `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0). It is a **logical
 * range**, not a pixel virtualisation and not a mount budget: it decides how far up the
 * history the reader has expanded (and therefore where the "显示更早" entry still points),
 * while the mounted rows themselves are whatever the viewport needs — see `VirtualTranscript.tsx`.
 *
 * Three collections stay deliberately distinct, and only the middle one lives here:
 *   - the full history the host loaded (owned by the runtime),
 *   - the logical range the reader has expanded (this module),
 *   - the physical mount the viewport shows (the virtual engine).
 * Expanding older history widens the logical range and never means those rows stay mounted.
 *
 * The reading position is the piece this host could not borrow as-is. PI stores a transcript
 * reading target with message ids; this contract only had `scroll`, a pixel offset that stops
 * meaning anything once the coordinate system is "the mounted rows" instead of "the whole
 * history". So `sessions.view` carries an anchor, and every row position here is expressed in
 * one content coordinate space so an anchor survives any mount, window, or layout change.
 */

/** How many trailing rows the very first commit mounts before the steady range opens. */
export const TRANSCRIPT_INITIAL_MOUNT = 15;
/** The steady logical range once hydrated: how far up the reader can scroll without revealing. */
export const TRANSCRIPT_WINDOW_MIN = 60;
/** How much older history one reveal (or reaching the top) adds to the logical range. */
export const TRANSCRIPT_WINDOW_STEP = 40;
/** How close to the top counts as "reached it" while revealing older pages. */
export const HISTORY_REVEAL_THRESHOLD_PX = 120;
/**
 * How many rows a live text selection may hold mounted. Native selection only exists in real DOM,
 * so the rows it spans must not recycle while the reader drags — and the drag must not be able to
 * pin the whole history either: past this budget the extra rows recycle and the selection stops growing.
 */
export const SELECTION_PIN_BUDGET = 40;

/**
 * The inclusive index span a selection from `from` to `to` holds mounted. Either direction of drag
 * gives the same span, and a span longer than the budget is cut at the end the drag started from,
 * so a long selection stops growing instead of pinning the whole history.
 */
export function selectionPinSpan(from: number, to: number): [number, number] {
  return to >= from
    ? [from, Math.min(to, from + SELECTION_PIN_BUDGET - 1)]
    : [Math.max(to, from - SELECTION_PIN_BUDGET + 1), from];
}

/**
 * The logical range the reader has opened. `mounted` is the trailing count that is
 * eligible to scroll through — the same number the "显示更早" entry counts down from —
 * NOT the rows physically in the DOM, which the virtual engine bounds to the viewport.
 */
export type TranscriptRange = {mounted: number; hiddenAbove: number; bounded: boolean};

export function reduceTranscriptWindow({historyLength, windowSize, initialCommit}: {historyLength: number; windowSize: number; initialCommit: boolean}): TranscriptRange {
  const length = Math.max(0, Math.round(historyLength));
  const budget = initialCommit ? TRANSCRIPT_INITIAL_MOUNT : Math.max(TRANSCRIPT_WINDOW_MIN, Math.round(windowSize));
  const mounted = Math.min(length, budget);
  return {mounted, hiddenAbove: length - mounted, bounded: length - mounted > 0};
}

/** The first index of the logical range: everything at or after this is scrollable history. */
export const logicalStartIndex = (historyLength: number, mounted: number): number => Math.max(0, Math.round(historyLength) - Math.max(0, Math.round(mounted)));

export function growTranscriptWindow(windowSize: number, historyLength: number): number {
  const current = Math.max(TRANSCRIPT_WINDOW_MIN, Math.round(windowSize));
  if (current >= historyLength) return current;
  return Math.min(historyLength, current + TRANSCRIPT_WINDOW_STEP);
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
