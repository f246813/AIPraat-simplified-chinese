// SPDX-License-Identifier: LGPL-3.0
// Extracted/adapted from PI-Desktop 0d47d26769ecbeca1c3ab56fa83b58a91de8190e
// apps/desktop/src/components/ContextMenu.tsx (renderer-only menu/focus/keyboard logic).
// Local changes: direct React portal, omit transcript/TeX selection, explicit point+trigger
// for sidebar left-click and right-click, scroll dismissal limited to the scroller that moves
// the trigger. No app-store, IPC, or Pi runtime.
// Source and license: ../../../third_party/pi-desktop/NOTICE.md
import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { placeContextMenu, type ContextMenuPlacement, type ContextMenuPoint } from './context-menu';
export type ContextMenuItem = {id: string; label: string; icon?: ReactNode; disabled?: boolean; danger?: boolean; separatorBefore?: boolean; submenu?: boolean; back?: boolean; keepOpen?: boolean; onSelect: () => void};
export type ContextMenuState = {label: string; items: ContextMenuItem[]; point: ContextMenuPoint; trigger: HTMLElement | null};
export function ContextMenu({state, onClose}: {state: ContextMenuState | null; onClose: () => void}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<ContextMenuPlacement | null>(null);
  const measure = useCallback((request: ContextMenuState) => {
    const menu = menuRef.current;
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    const next = placeContextMenu(request.point, {width: rect.width, height: rect.height}, {width: window.innerWidth, height: window.innerHeight});
    setPlacement(previous => previous && previous.top === next.top && previous.left === next.left ? previous : next);
  }, []);
  useEffect(() => { if (!state) setPlacement(null); }, [state]);
  useLayoutEffect(() => { if (state) measure(state); }, [measure, state]);
  useEffect(() => {
    if (!state || typeof ResizeObserver === 'undefined') return;
    const menu = menuRef.current;
    if (!menu) return;
    const observer = new ResizeObserver(() => measure(state));
    observer.observe(menu); return () => observer.disconnect();
  }, [measure, state]);
  useEffect(() => {
    if (!state) return;
    const interrupted = state.trigger || (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    return () => { if (interrupted?.isConnected) interrupted.focus(); };
  }, [state?.trigger, state?.point]);
  useEffect(() => {
    if (!state) return;
    const menu = menuRef.current;
    // The menu is placed in viewport coordinates around its trigger, so only a scroller that holds
    // that trigger can move it. These listeners observe every scroll in the page, including the
    // conversation adjusting its own reading position while a streamed reply arrives.
    const anchorMoves = (event: Event): boolean => {
      const trigger = state.trigger;
      if (!trigger) return true;
      const target = event.target;
      return target === window || target === document || (target instanceof Element && target.contains(trigger));
    };
    const onOutside = (event: Event) => {
      const target = event.target as Node | null;
      if (target && menu?.contains(target)) return;
      if (event.type === 'pointerdown' && target && state.trigger?.contains(target)) return;
      if (event.type === 'scroll' && !anchorMoves(event)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); onClose();
    };
    window.addEventListener('pointerdown', onOutside, true);
    window.addEventListener('contextmenu', onOutside, true);
    window.addEventListener('scroll', onOutside, true);
    window.addEventListener('resize', onClose);
    window.addEventListener('blur', onClose);
    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('pointerdown', onOutside, true);
      window.removeEventListener('contextmenu', onOutside, true);
      window.removeEventListener('scroll', onOutside, true);
      window.removeEventListener('resize', onClose);
      window.removeEventListener('blur', onClose);
      window.removeEventListener('keydown', onKeyDown, true);
    };
  }, [onClose, state]);
  useEffect(() => {
    if (!state || !placement) return;
    const frame = requestAnimationFrame(() => menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus());
    return () => cancelAnimationFrame(frame);
  }, [Boolean(placement), state?.point]);
  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
      const focused=(document.activeElement as HTMLElement)?.dataset.contextMenuItem;
      const item=event.key==='ArrowLeft'?state?.items.find(item=>item.back):state?.items.find(item=>item.id===focused&&item.submenu);
      if(item&&!item.disabled){event.preventDefault();event.stopPropagation();onClose();item.onSelect();}return;
    }
    if (event.key === 'Tab') { onClose(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)'));
    if (!items.length) return;
    event.preventDefault();
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  };
  if (!state || typeof document === 'undefined') return null;
  return createPortal(<div ref={menuRef} className={`context-menu${placement ? ' is-open' : ''}`} role="menu" aria-label={state.label} onKeyDown={onMenuKeyDown} onContextMenu={event => {event.preventDefault(); event.stopPropagation();}} style={placement ? {top: `${placement.top}px`, left: `${placement.left}px`} : undefined}>
    {state.items.map((item, index) => <Fragment key={item.id}>
      {item.separatorBefore && index > 0 ? <div className="context-menu-separator" role="separator"/> : null}
      <button type="button" role="menuitem" aria-haspopup={item.submenu?'menu':undefined} className={`context-menu-item${item.danger ? ' danger' : ''}`} data-context-menu-item={item.id} disabled={item.disabled} onClick={() => {if (!item.keepOpen) onClose(); item.onSelect();}}>
        {item.icon ? <span className="context-menu-icon" aria-hidden>{item.icon}</span> : null}<span className="context-menu-label">{item.label}</span>{item.submenu&&<span className="context-submenu-arrow" aria-hidden>›</span>}
      </button>
    </Fragment>)}
  </div>, document.body);
}
