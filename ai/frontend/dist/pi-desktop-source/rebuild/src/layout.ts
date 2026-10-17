/**
 * Window partition sizing.
 *
 * Interaction ported from PI-Desktop (fixed research revision
 * `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0):
 * `lib/sidebar-resize.ts`, `lib/sidebar-preferences.ts` and the shared
 * `chat-content-width.ts` / `components/ConversationWidthHandles.tsx` pair.
 * Only the interaction and its bounds are adopted; the implementation is
 * project-local, because this host cannot use PI's localStorage persistence:
 * pywebview runs WebView2 with `private_mode=True` and deletes the whole user
 * data folder on window close, so every layout value goes through the host
 * settings `preferences` whitelist instead.
 */

/** Expanded sidebar separator. The collapsed rail (68px) is not resizable. */
export const SIDEBAR_WIDTH_MIN = 240;
export const SIDEBAR_WIDTH_DEFAULT = 272;
export const SIDEBAR_WIDTH_MAX = 520;
export const SIDEBAR_WIDTH_STEP = 16;
/** A pointer width below this collapses the sidebar instead of leaving a cramped column. */
export const SIDEBAR_COLLAPSE_THRESHOLD = 160;
/** Hard floor for the chat column: the sidebar and the conversation band yield first. */
export const MAIN_PANE_MIN_WIDTH = 450;

/** Centered conversation band shared by the transcript, the empty state and the composer. */
export const CONVERSATION_WIDTH_MIN = 560;
export const CONVERSATION_WIDTH_DEFAULT = 720;
export const CONVERSATION_WIDTH_MAX = 4000;
/** Gutter kept on each side of the pane so the handles stay usable. */
export const CONVERSATION_WIDTH_GUTTER = 24;

const finite = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined;

export function clampSidebarWidth(value: unknown, max: number = SIDEBAR_WIDTH_MAX): number {
  const number = finite(value);
  if (number === undefined) return SIDEBAR_WIDTH_DEFAULT;
  const upper = Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(max)));
  return Math.round(Math.min(upper, Math.max(SIDEBAR_WIDTH_MIN, number)));
}

/** Live upper bound: the chat column keeps its floor, so the sidebar yields. */
export function sidebarWidthBudget(containerWidth: number): number {
  const width = Math.max(0, Math.round(finite(containerWidth) ?? 0));
  if (width <= 0) return SIDEBAR_WIDTH_MAX;
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, width - MAIN_PANE_MIN_WIDTH - 1));
}

export type SidebarPointerResize = {type: 'width'; width: number} | {type: 'collapse'};

export function sidebarPointerResize({startWidth, deltaX, maxWidth = SIDEBAR_WIDTH_MAX}: {startWidth: number; deltaX: number; maxWidth?: number}): SidebarPointerResize {
  const start = finite(startWidth); const delta = finite(deltaX);
  // A non-finite gesture must not write NaN into the layout or the host config.
  if (start === undefined || delta === undefined) return {type: 'width', width: clampSidebarWidth(SIDEBAR_WIDTH_DEFAULT, maxWidth)};
  const raw = start + delta;
  if (raw < SIDEBAR_COLLAPSE_THRESHOLD) return {type: 'collapse'};
  return {type: 'width', width: clampSidebarWidth(raw, maxWidth)};
}

export function sidebarResetWidth(maxWidth: number = SIDEBAR_WIDTH_MAX): number {
  return clampSidebarWidth(SIDEBAR_WIDTH_DEFAULT, maxWidth);
}

/** Absent or invalid persists as the default; the live pane may still draw narrower. */
export function resolveConversationWidth(value: unknown): number {
  const number = finite(value);
  if (number === undefined) return CONVERSATION_WIDTH_DEFAULT;
  return Math.max(CONVERSATION_WIDTH_MIN, Math.round(number));
}

/** Clamp a drag or keyboard candidate against the live pane. */
export function clampConversationWidth(width: number, paneWidth: number): number {
  const available = Math.max(0, Math.round(finite(paneWidth) ?? 0) - 2 * CONVERSATION_WIDTH_GUTTER);
  if (available <= 0) return CONVERSATION_WIDTH_MIN;
  const floor = Math.min(CONVERSATION_WIDTH_MIN, available);
  return Math.min(available, Math.max(floor, Math.round(width)));
}

export type ConversationResizeSide = 'left' | 'right';

/** Both handles move one centered band, so 1px of pointer travel is 2px of width. */
export function conversationWidthFromDrag(args: {side: ConversationResizeSide; startWidth: number; startClientX: number; clientX: number; paneWidth: number}): number {
  const delta = args.clientX - args.startClientX;
  const next = args.side === 'left' ? args.startWidth - 2 * delta : args.startWidth + 2 * delta;
  return clampConversationWidth(next, args.paneWidth);
}

/** Keyboard step shared by the sidebar separator and the band handles. */
export const resizeStep = (shiftKey: boolean) => (shiftKey ? SIDEBAR_WIDTH_STEP * 2 : SIDEBAR_WIDTH_STEP);

export type ResizeKeyIntent = 'wider' | 'narrower' | 'default' | 'maximum' | null;

/**
 * One keyboard map for both separators. `towardWider` says which arrow key moves
 * this particular edge, because the sidebar separator grows to the right while
 * the left band handle grows to the left.
 */
export function resizeKeyIntent(event: {key: string; shiftKey?: boolean}, towardWider: 'ArrowLeft' | 'ArrowRight'): ResizeKeyIntent {
  if (event.key === towardWider) return 'wider';
  if (event.key === (towardWider === 'ArrowLeft' ? 'ArrowRight' : 'ArrowLeft')) return 'narrower';
  if (event.key === 'Home') return 'default';
  if (event.key === 'End') return 'maximum';
  return null;
}
