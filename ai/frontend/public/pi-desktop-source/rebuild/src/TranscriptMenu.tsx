/**
 * Wiring for the transcript context menu: the pure entry vocabulary lives in
 * `transcript-menu.ts`, this module only maps ids to icons and to handlers, and
 * reads the browser selection the way PI-Desktop's copy does.
 */
import type { ReactNode } from 'react';
import { ArrowDown, ArrowUp, Copy, FileJson, TextSelect } from 'lucide-react';
import type { ContextMenuItem } from './pi/ContextMenu';
import type { ContextMenuPoint } from './pi/context-menu';
import { copyPayload, type TranscriptMenuAction, type TranscriptMenuEntry } from './transcript-menu';

const icons: Record<TranscriptMenuAction, ReactNode> = {
  'copy-message': <Copy size={14}/>,
  'select-message': <TextSelect size={14}/>,
  'copy-evidence': <FileJson size={14}/>,
  'copy-conversation': <Copy size={14}/>,
  'select-conversation': <TextSelect size={14}/>,
  'scroll-top': <ArrowUp size={14}/>,
  'scroll-bottom': <ArrowDown size={14}/>,
};

export type TranscriptMenuState = {label: string; point: ContextMenuPoint; trigger: HTMLElement | null; items: ContextMenuItem[]};

export const transcriptMenuItems = (entries: TranscriptMenuEntry[], run: (action: TranscriptMenuAction) => void): ContextMenuItem[] =>
  entries.map(entry => ({id: entry.id, label: entry.label, icon: icons[entry.id], disabled: entry.disabled, separatorBefore: entry.separatorBefore, onSelect: () => run(entry.id)}));

/** The selection is only adopted when it actually sits inside the row the menu was opened on. */
export function selectionInside(trigger: HTMLElement | null): string | undefined {
  const selection = window.getSelection?.();
  if (!trigger || !selection || selection.isCollapsed || !selection.rangeCount) return undefined;
  if (!trigger.contains(selection.getRangeAt(0).commonAncestorContainer)) return undefined;
  return selection.toString();
}

export function selectContents(element: HTMLElement | null) {
  const selection = window.getSelection?.();
  if (!element || !selection) return;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
}

/** Copy through the same payload rule the menu item used, so tests can assert it without a clipboard. */
export const messageCopyPayload = (trigger: HTMLElement | null, text: string) => copyPayload(selectionInside(trigger), text);
