/**
 * Conversation minimap: the long-history navigation rail.
 *
 * Interaction ported from PI-Desktop (`components/ConversationMinimap.tsx`,
 * `lib/conversation-minimap.ts`, fixed revision
 * `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`, LGPL-3.0), adapted to this data model:
 * PI merges each assistant turn's stream fragments into one marker, while here the host
 * already delivers one settled message per turn, so a marker is simply a non-empty row.
 *
 * Three deliberate differences, all because of the mount window and the fixed track:
 * the rail describes the whole **expanded logical history** and takes its positions from the
 * virtual engine's row metadata (a withheld row still navigates, through the unified entry);
 * the "earlier history" dash reveals the next page instead of loading an older one; and the
 * dashes are grouped to the track's capacity, because a row per message would overflow the
 * rail long before the reader could aim at one.
 */

export const CONVERSATION_MINIMAP_PREVIEW_MAX_CHARS = 280;
/** A rail shorter than its content only appears once the transcript actually overflows. */
export const OVERFLOW_EPSILON_PX = 1;
export const MINIMAP_MAGNIFY_RADIUS = 46;
export const MINIMAP_MAGNIFY_BOOST = 1.3;
export const MINIMAP_POPOVER_SNAP = 24;
export const MINIMAP_POPOVER_HEIGHT = 132;
/** Where the "you are reading here" line sits inside the viewport. */
export const MINIMAP_ANCHOR_RATIO = 0.3;
export const MINIMAP_JUMP_OFFSET_PX = 24;
/** The rail's own vertical insets, mirrored from `.minimap-rail{top;bottom}` in styles.css. */
export const MINIMAP_TRACK_INSET_PX = 8;
/** One dash row, mirrored from `.minimap-marker{height}`. */
export const MINIMAP_DASH_PX = 8;
/** An open group lists at most this many messages, so the panel cannot outgrow the track. */
export const MINIMAP_GROUP_LIST_LIMIT = 8;
/** One list row plus the panel's range heading and padding, for clamping its top edge. */
export const MINIMAP_GROUP_ROW_PX = 27;
export const MINIMAP_GROUP_PANEL_CHROME_PX = 38;

export type ConversationMarker = {id: string; role: 'user' | 'assistant'; preview: string};

export const conversationMarkers = (messages: {id: string; role: string; content: string}[]): ConversationMarker[] =>
  messages
    .filter(message => (message.role === 'user' || message.role === 'assistant') && message.content.trim())
    .map(message => ({id: message.id, role: message.role as 'user' | 'assistant', preview: message.content.trim().slice(0, CONVERSATION_MINIMAP_PREVIEW_MAX_CHARS)}));

export function shouldRenderMinimap({markerCount, overflows, hasEarlier}: {markerCount: number; overflows: boolean; hasEarlier: boolean}): boolean {
  return hasEarlier || (markerCount >= 2 && overflows);
}

/** Cosine falloff across the rail, so the dash under the pointer widens and its neighbours follow. */
export function magnifyScale(distance: number | null): number {
  if (distance === null || !Number.isFinite(distance) || Math.abs(distance) >= MINIMAP_MAGNIFY_RADIUS) return 1;
  return 1 + MINIMAP_MAGNIFY_BOOST * Math.cos((Math.abs(distance) / MINIMAP_MAGNIFY_RADIUS) * (Math.PI / 2));
}

/** The preview bubbles stay inside the rail and leave a readable gap above the nearest dash. */
export const popoverTop = (center: number, railHeight: number, panelHeight = MINIMAP_POPOVER_HEIGHT): number =>
  Math.round(Math.min(Math.max(center - 36, 0), Math.max(railHeight - panelHeight, 0)));

/**
 * The marker the reading line sits on, found by binary search over the engine's rows rather
 * than by walking the markers or the rail: ordinary scrolling only asks this one question.
 * A row that carries no marker (an empty placeholder turn) reports the nearest marker above it,
 * and a line above every row reports the first one.
 */
export function activeMarkerAt({rows, markerIndexByRow, anchorLine}: {rows: {id: string; top: number}[]; markerIndexByRow: Map<string, number>; anchorLine: number}): number {
  if (!rows.length) return 0;
  let low = 0; let high = rows.length - 1; let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (rows[mid].top <= anchorLine) { found = mid; low = mid + 1; } else high = mid - 1;
  }
  for (let index = Math.max(0, found); index >= 0; index--) {
    const marker = markerIndexByRow.get(rows[index].id);
    if (marker !== undefined) return marker;
  }
  for (const row of rows) {
    const marker = markerIndexByRow.get(row.id);
    if (marker !== undefined) return marker;
  }
  return 0;
}

/** The rail's gap shrinks as the rail fills up, so a dense history never overflows the rail. */
export const markerGap = (count: number): number => Math.min(2, Math.max(0, 4 - count * 0.05));

/**
 * How many dashes the track holds, given that `extra` further dashes (the "earlier history"
 * entry) share it. The gap a rail uses depends on how many dashes it holds and that number
 * depends on the gap, so the two are solved together: start at the densest packing, then let
 * the wider gap a shorter rail can afford reduce the count until it stops moving.
 */
export function trackCapacity(height: number, extra = 0): number {
  if (!Number.isFinite(height) || height < MINIMAP_DASH_PX) return 1;
  let total = Math.floor(height / MINIMAP_DASH_PX);
  for (let step = 0; step < 8; step++) {
    const gap = markerGap(total);
    const fits = Math.max(1, Math.floor((height + gap) / (MINIMAP_DASH_PX + gap)));
    if (fits >= total) break;
    total = fits;
  }
  return Math.max(1, total - extra);
}

/**
 * A rail dash: one message while the history fits the track, then a contiguous run of them.
 * `from` and `to` are inclusive indices into the logical marker list, so the range a group
 * announces survives any later regrouping.
 */
export type MarkerGroup = {key: string; role: 'user' | 'assistant'; from: number; to: number; markers: ConversationMarker[]};

/**
 * Short histories keep one dash per message. A long one is split into at most `capacity`
 * contiguous groups whose sizes differ by at most one, and each group is keyed by its leading
 * message ID so a React list can hand the same dash to the same message across re-renders.
 */
export function groupMarkers(markers: ConversationMarker[], capacity: number): MarkerGroup[] {
  const size = Math.max(1, Math.ceil(markers.length / Math.max(1, capacity)));
  const groups: MarkerGroup[] = [];
  for (let from = 0; from < markers.length; from += size) {
    const run = markers.slice(from, Math.min(markers.length, from + size));
    groups.push({key: run[0].id, role: run[0].role, from, to: from + run.length - 1, markers: run});
  }
  return groups;
}

/** The group holding a marker index; groups are contiguous, so the ranges are searchable. */
export function groupIndexForMarker(groups: MarkerGroup[], markerIndex: number): number {
  let low = 0; let high = groups.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (markerIndex < groups[mid].from) high = mid - 1;
    else if (markerIndex > groups[mid].to) low = mid + 1;
    else return mid;
  }
  return -1;
}

/**
 * The bounded in-group list. The window shifts to hold `anchorId` — the message the reader was
 * on when the group was activated — so opening a group never hides the row being read.
 */
export function groupWindow(markers: ConversationMarker[], anchorId: string | null, limit = MINIMAP_GROUP_LIST_LIMIT): ConversationMarker[] {
  if (limit <= 0 || markers.length <= limit) return markers;
  const anchor = anchorId ? markers.findIndex(marker => marker.id === anchorId) : -1;
  const start = anchor < 0 ? 0 : Math.min(Math.max(anchor - Math.floor((limit - 1) / 2), 0), markers.length - limit);
  return markers.slice(start, start + limit);
}

/**
 * What a group announces: the span of history it stands for, counted from the start of the session
 * (`offset` is where the expanded range begins), so the numbers match the `aria-posinset` a reader
 * hears on the rows themselves rather than restarting at one inside every new page.
 */
export const groupRangeLabel = (group: MarkerGroup, offset = 0): string => group.from === group.to
  ? `第 ${group.from + offset + 1} 条消息`
  : `第 ${group.from + offset + 1}–${group.to + offset + 1} 条消息`;

/** The height the in-group panel takes, for keeping its top edge inside the track. */
export const groupPanelHeight = (count: number): number => MINIMAP_GROUP_PANEL_CHROME_PX + Math.max(1, count) * MINIMAP_GROUP_ROW_PX;

export const prefersReducedMotion = (): boolean =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
