import { expect, it } from 'vitest';
import {
  activeMarkerAt, conversationMarkers, groupIndexForMarker, groupMarkers, groupPanelHeight, groupRangeLabel,
  groupWindow, magnifyScale, markerGap, popoverTop, shouldRenderMinimap, trackCapacity,
  CONVERSATION_MINIMAP_PREVIEW_MAX_CHARS, MINIMAP_DASH_PX, MINIMAP_GROUP_LIST_LIMIT, MINIMAP_MAGNIFY_BOOST,
  MINIMAP_POPOVER_HEIGHT, type ConversationMarker,
} from '../src/minimap';

const turn = (id: string, role: 'user' | 'assistant', content: string): ConversationMarker => ({id, role, preview: content});
const turns = (count: number): ConversationMarker[] => Array.from({length: count}, (_, index) => turn(`m${index}`, index % 2 ? 'assistant' : 'user', `第 ${index + 1} 条`));

it('gives every non-empty turn a marker and skips the placeholders', () => {
  const markers = conversationMarkers([
    {id: 'u1', role: 'user', content: '  第一个问题  '},
    {id: 'a1', role: 'assistant', content: ''},
    {id: 'a2', role: 'assistant', content: '回答'},
    {id: 'x', role: 'system', content: '不该出现'},
    {id: 'u2', role: 'user', content: '   '},
    {id: 'u3', role: 'user', content: 'x'.repeat(400)},
  ]);
  expect(markers.map(marker => marker.id)).toEqual(['u1', 'a2', 'u3']);
  expect(markers[0]).toEqual({id: 'u1', role: 'user', preview: '第一个问题'});
  expect(markers[2].preview).toHaveLength(CONVERSATION_MINIMAP_PREVIEW_MAX_CHARS);
  expect(conversationMarkers([])).toEqual([]);
});

it('hides the rail until the transcript actually overflows', () => {
  expect(shouldRenderMinimap({markerCount: 2, overflows: false, hasEarlier: false})).toBe(false);
  expect(shouldRenderMinimap({markerCount: 1, overflows: true, hasEarlier: false})).toBe(false);
  expect(shouldRenderMinimap({markerCount: 2, overflows: true, hasEarlier: false})).toBe(true);
  // A withheld page is navigable even when the mounted tail happens to fit.
  expect(shouldRenderMinimap({markerCount: 0, overflows: false, hasEarlier: true})).toBe(true);
});

it('falls off smoothly from the pointer and stops at the radius', () => {
  expect(magnifyScale(0)).toBeCloseTo(1 + MINIMAP_MAGNIFY_BOOST, 5);
  expect(magnifyScale(null)).toBe(1);
  expect(magnifyScale(Number.NaN)).toBe(1);
  expect(magnifyScale(46)).toBe(1);
  expect(magnifyScale(-46)).toBe(1);
  expect(magnifyScale(400)).toBe(1);
  // Symmetric, and monotonically smaller further away.
  expect(magnifyScale(-12)).toBeCloseTo(magnifyScale(12), 6);
  expect(magnifyScale(12)).toBeGreaterThan(magnifyScale(30));
});

/** The rail is a centered flex column, so its content is the dashes plus the gaps between them. */
const packedHeight = (total: number): number => {
  const gap = markerGap(total);
  return total * MINIMAP_DASH_PX + Math.max(0, total - 1) * gap;
};

it('sizes the rail to the dashes the track can actually hold', () => {
  for (const height of [120, 584, 744]) {
    // The whole rail is the marker dashes plus the withheld-page entry sharing the track.
    for (const extra of [0, 1]) {
      const total = trackCapacity(height, extra) + extra;
      expect(packedHeight(total), `${height} +${extra}`).toBeLessThanOrEqual(height);
      // One dash more is exactly what grouping exists to prevent.
      expect(packedHeight(total + 1), `${height} +${extra}`).toBeGreaterThan(height);
    }
  }
  // A track shorter than a dash, or not measured yet, still yields one usable slot.
  expect(trackCapacity(0)).toBe(1);
  expect(trackCapacity(-20)).toBe(1);
  expect(trackCapacity(Number.NaN)).toBe(1);
  // The withheld-page dash occupies a slot too, so it costs the markers one.
  expect(trackCapacity(584, 1)).toBe(trackCapacity(584) - 1);
});

it('keeps one dash per message until the history outgrows the track', () => {
  const markers = turns(6);
  const exact = groupMarkers(markers, 6);
  expect(exact).toHaveLength(6);
  expect(exact.map(group => group.key)).toEqual(markers.map(marker => marker.id));
  expect(exact[2]).toMatchObject({from: 2, to: 2, markers: [markers[2]]});
  expect(groupMarkers([], 4)).toEqual([]);
});

it('splits a long history into contiguous groups that fit the track', () => {
  const markers = turns(121);
  const groups = groupMarkers(markers, 68);
  expect(groups.length).toBeLessThanOrEqual(68);
  // Every message is represented exactly once, in order, and the sizes differ by at most one.
  expect(groups.flatMap(group => group.markers.map(marker => marker.id))).toEqual(markers.map(marker => marker.id));
  const sizes = groups.map(group => group.to - group.from + 1);
  expect(Math.max(...sizes) - Math.min(...sizes)).toBeLessThanOrEqual(1);
  expect(groups[0]).toMatchObject({from: 0, to: sizes[0] - 1, role: 'user'});
  // Asking for fewer slots than there are messages still covers the whole range.
  expect(groupMarkers(markers, 1)).toHaveLength(1);
  expect(groupMarkers(markers, 0)).toHaveLength(1);
});

it('finds the group holding a marker index', () => {
  const groups = groupMarkers(turns(10), 3);
  for (let marker = 0; marker < 10; marker++) {
    const index = groupIndexForMarker(groups, marker);
    expect(index).toBeGreaterThanOrEqual(0);
    expect(marker).toBeGreaterThanOrEqual(groups[index].from);
    expect(marker).toBeLessThanOrEqual(groups[index].to);
  }
  expect(groupIndexForMarker(groups, -1)).toBe(-1);
  expect(groupIndexForMarker(groups, 10)).toBe(-1);
  expect(groupIndexForMarker([], 0)).toBe(-1);
});

it('announces the message range a group stands for', () => {
  const groups = groupMarkers(turns(5), 2);
  expect(groupRangeLabel(groups[0])).toBe('第 1–3 条消息');
  expect(groupRangeLabel(groups[1])).toBe('第 4–5 条消息');
  expect(groupRangeLabel(groupMarkers(turns(3), 3)[1])).toBe('第 2 条消息');
  // The same group counted from part-way into the session keeps the numbering the rows use.
  expect(groupRangeLabel(groups[0], 61)).toBe('第 62–64 条消息');
  expect(groupRangeLabel(groups[1], 61)).toBe('第 65–66 条消息');
  expect(groupPanelHeight(2)).toBeGreaterThan(groupPanelHeight(1));
});

it('keeps the in-group list bounded and centred on the message being read', () => {
  const markers = turns(30);
  expect(groupWindow(markers, null, 8)).toEqual(markers.slice(0, 8));
  expect(groupWindow(markers, 'm15', 8)).toEqual(markers.slice(12, 20));
  // The window is clamped to the range, so the reader never loses the first or last message.
  expect(groupWindow(markers, 'm0', 8)).toEqual(markers.slice(0, 8));
  expect(groupWindow(markers, 'm29', 8)).toEqual(markers.slice(22, 30));
  expect(groupWindow(markers, 'gone', 8)).toEqual(markers.slice(0, 8));
  // A group that already fits is listed whole, and the shared limit stays a real bound.
  expect(groupWindow(markers.slice(0, 5), 'm3')).toHaveLength(5);
  expect(groupWindow(markers, 'm15')).toHaveLength(MINIMAP_GROUP_LIST_LIMIT);
});

it('keeps the preview bubble inside the rail', () => {
  expect(popoverTop(10, 400)).toBe(0);
  expect(popoverTop(200, 400)).toBe(164);
  expect(popoverTop(1000, 400)).toBe(400 - MINIMAP_POPOVER_HEIGHT);
  // A rail shorter than the bubble still yields a non-negative offset.
  expect(popoverTop(50, 100)).toBe(0);
  // A taller panel (the in-group list) is clamped by its own height.
  const tall = groupPanelHeight(MINIMAP_GROUP_LIST_LIMIT);
  expect(popoverTop(1000, 400, tall)).toBe(400 - tall);
  expect(popoverTop(1000, 100, tall)).toBe(0);
});

it('marks the dash the reading line sits on', () => {
  const markers = turns(3);
  const markerIndexByRow = new Map(markers.map((marker, index) => [marker.id, index]));
  const rows = [{id: 'm0', top: 0}, {id: 'm1', top: 500}, {id: 'm2', top: 1000}];
  const at = (anchorLine: number) => activeMarkerAt({rows, markerIndexByRow, anchorLine});
  expect(at(600)).toBe(1);
  expect(at(500)).toBe(1);
  expect(at(499)).toBe(0);
  expect(at(-50)).toBe(0);
  expect(at(5000)).toBe(2);
  expect(activeMarkerAt({rows: [], markerIndexByRow, anchorLine: 10})).toBe(0);
  // A placeholder row the reader stopped on reports the nearest turn above it, not nothing.
  const withGap = [{id: 'm0', top: 0}, {id: 'pending', top: 400}, {id: 'm1', top: 500}];
  expect(activeMarkerAt({rows: withGap, markerIndexByRow, anchorLine: 450})).toBe(0);
  expect(activeMarkerAt({rows: withGap, markerIndexByRow, anchorLine: 500})).toBe(1);
});

it('tightens the rail gap as it fills up so a dense history never overflows', () => {
  expect(markerGap(0)).toBe(2);
  expect(markerGap(40)).toBe(2);
  expect(markerGap(60)).toBe(1);
  expect(markerGap(200)).toBe(0);
});
