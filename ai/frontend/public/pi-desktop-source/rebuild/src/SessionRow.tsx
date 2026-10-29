import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { Archive, ArchiveRestore, Folder, GitFork, LoaderCircle, MoreHorizontal, Pencil, Pin, PinOff, Trash2, X } from 'lucide-react';
import { ChatStore, shallowEqual, useChatSelector } from './store';
import { activeTask, readOnly, type Session } from './types';
import { statusLabel } from './ui';
import { ContextMenu, type ContextMenuState } from './pi/ContextMenu';

export function SessionRow({store, session, selected, onRename, onDelete, multiSelected=false, onSelect, onHover, onHidePreview, onContextSelection, onMove, onDropBefore}: {
  store: ChatStore; session: Session; selected: boolean;
  onRename: (session: Session) => void; onDelete: (session: Session) => void;
  multiSelected?: boolean; onSelect?: (event: MouseEvent, id: string) => boolean;
  onMove?: (session:Session,menu:Pick<ContextMenuState,'point'|'trigger'>,back:()=>void)=>void;
  onDropBefore?: (id:string,beforeId:string)=>void;
  onHover?: (target: HTMLElement) => void; onHidePreview?: () => void; onContextSelection?: (id: string) => void;
}) {
  // Consume only this row's fields: a background session's text increment or another
  // row's activity never re-renders this one. The running task is read through the
  // subscription so the spinner stays reactive without the whole snapshot.
  const {connected, organizationBusy, submitting, running} = useChatSelector(store, state => ({
    connected: state.connected,
    organizationBusy: state.organizationBusy,
    submitting: state.submitting[session.id],
    running: Object.values(state.tasks).find(t => t.sessionId === session.id && activeTask(t)),
  }), shallowEqual);
  const trigger = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState<Pick<ContextMenuState, 'point' | 'trigger'> | null>(null);
  const [pinning, setPinning] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const prefetch=useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancelPrefetch=() => { clearTimeout(prefetch.current); prefetch.current=undefined; };
  const loadPreview=() => { if (!store.getSnapshot().messages[session.id]) void store.load(session.id).catch(store.report); };
  useEffect(() => () => clearTimeout(prefetch.current),[session.id]);
  const close = useCallback(() => { setMenu(null); setConfirmDelete(false); }, []);
  const readonly = readOnly(session);
  const cannotDelete = Boolean(running) || Boolean(submitting);
  const menuState = useMemo<ContextMenuState | null>(() => menu ? {
    ...menu, label: `会话操作 ${session.title}`,
    items: [
      {id:'rename-session', label:'重命名', icon:<Pencil size={14}/>, disabled:readonly, onSelect:() => onRename(session)},
      {id:'toggle-session-pin', label:session.pinned ? '取消置顶' : '置顶', icon:session.pinned ? <PinOff size={14}/> : <Pin size={14}/>, disabled:readonly || pinning || !connected, onSelect:() => {
        setPinning(true); void store.pin(session.id, !session.pinned).catch(store.report).finally(() => setPinning(false));
      }},
      {id:'move-session-section',label:'分区',icon:<Folder size={14}/>,submenu:true,disabled:!onMove||organizationBusy||!connected,onSelect:()=>{if(menu)onMove?.(session,menu,()=>setMenu(menu));}},
      {id:'fork-session',label:'分叉会话',icon:<GitFork size={14}/>,disabled:organizationBusy||!connected,onSelect:()=>{void store.fork(session.id).catch(store.report);}},
      {id:'archive-session',label:session.archived?'恢复会话':'归档会话',icon:session.archived?<ArchiveRestore size={14}/>:<Archive size={14}/>,disabled:organizationBusy||!connected||(!session.archived&&(Boolean(running)||submitting)),onSelect:()=>{void store.archive(session.id,!session.archived).catch(store.report);}},
      {id:'delete-session', label:confirmDelete ? '确认删除？' : '删除', icon:confirmDelete ? <X size={14}/> : <Trash2 size={14}/>, danger:true, separatorBefore:true, disabled:cannotDelete, keepOpen:!confirmDelete, onSelect:() => { if (confirmDelete) onDelete(session); else setConfirmDelete(true); }},
    ],
  } : null, [menu, confirmDelete, session, readonly, cannotDelete, running, pinning, connected, organizationBusy, submitting, store, onRename, onDelete, onMove]);
  function open(event: MouseEvent<HTMLElement>, pointer = false) {
    event.preventDefault(); event.stopPropagation();
    onHidePreview?.();
    if (!pointer && menu) { close(); return; }
    const rect = trigger.current?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect();
    // PI-Desktop Sidebar.tsx: left-click starts beside the trigger; right-click at the pointer.
    setMenu({point:pointer && (event.clientX || event.clientY) ? {x:event.clientX + 4,y:event.clientY + 4} : {x:rect.right + 4,y:rect.bottom + 4}, trigger:trigger.current});
  }
  // Pi Sidebar::renderSessionRows: compact row, prefetch, hover card, modifiers
  // and action trigger. Host operations and readonly/running guards stay local.
  return <div className={`thread-item session-row ${selected ? 'active' : ''} ${multiSelected ? 'selected' : ''} ${menu ? 'menu-open' : ''}`} draggable={!organizationBusy} onDragStart={event=>{cancelPrefetch();onHidePreview?.();event.dataTransfer.setData('application/x-aipraat-history',session.id);event.dataTransfer.effectAllowed='move';}} onDragOver={event=>{if(onDropBefore&&Array.from(event.dataTransfer.types).includes('application/x-aipraat-history')){event.preventDefault();event.dataTransfer.dropEffect='move';}}} onDrop={event=>{const id=event.dataTransfer.getData('application/x-aipraat-history');if(id&&onDropBefore){event.preventDefault();event.stopPropagation();if(id!==session.id)onDropBefore(id,session.id);}}} data-sidebar-session-row={session.id} data-session-id={session.id} data-pinned={Boolean(session.pinned)} data-multi-selected={multiSelected} onContextMenu={event => { onContextSelection?.(session.id); open(event, true); }}>
    <button className="thread-item-main session-select" aria-current={selected?'page':undefined} title={session.title} onPointerEnter={() => { cancelPrefetch(); prefetch.current=setTimeout(loadPreview,120); }} onPointerLeave={cancelPrefetch} onMouseEnter={event=>onHover?.(event.currentTarget)} onMouseLeave={onHidePreview} onFocus={event=>{ loadPreview(); onHover?.(event.currentTarget); }} onBlur={onHidePreview} onClick={event => { if (onSelect?.(event,session.id)) return; cancelPrefetch(); onHidePreview?.(); void store.select(session.id).catch(store.report); }}>
      {session.pinned && <Pin className="thread-item-pin" size={11} aria-label="已置顶"/>}
      <span className="thread-item-title"><strong>{session.title || '未命名会话'}</strong></span>
      {readonly && <span className="thread-item-source">只读</span>}
      {running && <LoaderCircle className="spin" size={13} aria-label={statusLabel(running.status)}/>}
    </button>
    <button ref={trigger} type="button" className="thread-item-more session-more icon-button" aria-label={`会话操作 ${session.title}`} title={readonly ? '旧记录只读' : '会话操作'} aria-haspopup="menu" aria-expanded={Boolean(menu)} onClick={event => open(event)}><MoreHorizontal size={14}/></button>
    <ContextMenu state={menuState} onClose={close}/>
  </div>;
}
