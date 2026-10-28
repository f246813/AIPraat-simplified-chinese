// SPDX-License-Identifier: LGPL-3.0
// Pi SessionHoverCard renderer adapted to Praat session details. Original is shipped.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChatStore, shallowEqual, useChatSelector } from '../../store';
import { readOnly } from '../../types';
import { statusLabel } from '../../ui';
import { positionSessionHoverCard, sessionPreview } from './preview';
import type { SessionHoverCardData } from './useSessionHoverCard';

export function SessionHoverCard({store, card, keepVisible, scheduleHide, onOpen}: {
  store: ChatStore; card: SessionHoverCardData; keepVisible: () => void; scheduleHide: () => void; onOpen: () => void;
}) {
  const state=useChatSelector(store, s => ({messages: s.messages[card.session.id], loading: s.loading[card.session.id]}), shallowEqual); const ref=useRef<HTMLDivElement>(null);
  const [position,setPosition]=useState<{left:number;top:number}>();
  useEffect(() => { if (!store.getSnapshot().messages[card.session.id]) void store.load(card.session.id).catch(store.report); },[store,card.session.id]);
  useLayoutEffect(() => {
    const element=ref.current; if (!element) return;
    const place=() => setPosition(positionSessionHoverCard(card.target.getBoundingClientRect(),{width:element.offsetWidth,height:element.offsetHeight},{width:innerWidth,height:innerHeight}));
    place(); const observer=new ResizeObserver(place); observer.observe(element); return () => observer.disconnect();
  },[card.target]);
  const messages=state.messages || [];
  const question=[...messages].reverse().find(m=>m.role==='user');
  const answer=[...messages].reverse().find(m=>m.role==='assistant');
  const task=store.running(card.session.id);
  return createPortal(<div ref={ref} id={`session-hover-${card.session.id}`} className="sidebar-session-hover-card" role="dialog" aria-label={`会话预览 ${card.session.title}`} style={{...position,visibility:position?'visible':'hidden'}} onMouseEnter={keepVisible} onMouseLeave={scheduleHide} onPointerDown={event=>event.stopPropagation()} onFocusCapture={keepVisible} onBlurCapture={scheduleHide}>
    <div className="sidebar-session-hover-card-title">{card.session.title}</div>
    <div className="sidebar-session-hover-card-status">{readOnly(card.session)?'旧记录 · 只读':task?statusLabel(task.status):'可恢复续聊'}</div>
    <div className="sidebar-session-hover-card-meta">{new Date(card.session.updated).toLocaleString('zh-CN')} · {messages.length} 条消息</div>
    {state.loading ? <p>正在读取历史…</p> : <>
      {question && <section className="sidebar-session-hover-card-section"><div className="sidebar-session-hover-card-section-label">最近的问题</div><p className="sidebar-session-hover-card-preview">{sessionPreview(question.content)}</p></section>}
      {answer && <section className="sidebar-session-hover-card-section"><div className="sidebar-session-hover-card-section-label">最近的回答</div><p className="sidebar-session-hover-card-preview">{sessionPreview(answer.content)}</p></section>}
      {!messages.length && <p className="sidebar-session-hover-card-preview">暂无消息</p>}
    </>}
    <button className="sidebar-preview-open" onClick={onOpen}>打开会话</button>
  </div>,document.body);
}
