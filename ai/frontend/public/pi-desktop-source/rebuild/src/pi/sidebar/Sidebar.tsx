// SPDX-License-Identifier: LGPL-3.0
// PI-Desktop Sidebar.tsx session renderer/controller extraction, pin 0d47d26.
// Imports, data projection and host operations adapted to Praat; full original
// is preserved in third_party/pi-desktop/upstream/sidebar/Sidebar.tsx.
import {useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent} from 'react';
import {AudioLines, ChevronDown, ChevronLeft, ChevronRight,FolderPlus, LoaderCircle, Plus, Search, Settings, Trash2, X} from 'lucide-react';
import {ChatStore, useChatState} from '../../store';
import {activeTask, type Session, type RetainedTimeGroup} from '../../types';
import {IconButton} from '../../ui';
import {SessionRow} from '../../SessionRow';
import {SidebarResizer} from '../../Panes';
import {getGlobalPinnedSessions, groupSidebarSessionsByTime} from './sidebar-session-groups';
import {useSessionHoverCard} from './useSessionHoverCard';
import {SessionHoverCard} from './SessionHoverCard';
import {useArmedDelete} from './use-armed-delete';
import {HistorySectionHeader,ordinarySection,useHistoryOrganization} from '../../HistoryOrganization';
import type {SessionSummary} from './types';
import {useSessionSearch} from '../../codex/useSessionSearch';
import {SidebarOptions,sidebarSorts,sidebarSortOrder,type SidebarSort,type SidebarGrouping} from '../../SidebarOptions';
import {groupSidebarOrigins} from '../../codex/sidebar-origins';
import './upstream.css';
import './styles.css';

const groupLabels={today:'今天',yesterday:'昨天',thisWeek:'近 7 天',older14d:'近 14 天',archived:'更早'};

export function SessionSidebar({store, expanded, onToggle, width, maxWidth, onPreviewWidth, onCommitWidth, onTasks, onSettings, onRename, onDelete}: {
  store:ChatStore; expanded:boolean; onToggle:()=>void; width:number; maxWidth:number;
  onPreviewWidth:(width:number)=>void; onCommitWidth:(width:number)=>void;
  onTasks:()=>void; onSettings:()=>void; onRename:(session:Session)=>void; onDelete:(session:Session)=>void;
}) {
  const state=useChatState(store);
  const archived=false;
  const [query,setQuery]=useState('');
  const search=useSessionSearch(store,query,archived,state.sessions);
  const [collapsed,setCollapsed]=useState<Record<string,boolean>>({});
  const [selectedIds,setSelectedIds]=useState<Set<string>>(new Set());
  const lastClickedIdRef=useRef<string|null>(null);
  const [optionsOpen,setOptionsOpen]=useState(false);
  const [deleting,setDeleting]=useState(false);
  const {armed,setArmed}=useArmedDelete();
  const hover=useSessionHoverCard();
  const create=(section:string|null)=>{void store.create(section).catch(store.report);};
  const organization=useHistoryOrganization(store,archived,create);
  const preference=String(state.boot?.settings.preferences.sidebar_sort || 'recent');
  const sort:SidebarSort=Object.hasOwn(sidebarSorts,preference)?preference as SidebarSort:'recent';
  const groupingValue=state.boot?.settings.preferences.sidebar_grouping;
  const grouping:SidebarGrouping=groupingValue==='list'||groupingValue==='connection'?groupingValue:'project';
  const tasks=Object.values(state.tasks).filter(activeTask);

  useEffect(() => {
    const key=(event:globalThis.KeyboardEvent)=>{
      if(event.isComposing||event.keyCode===229)return;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='b'&&!event.altKey && !(event.target as HTMLElement)?.closest('input,textarea,[contenteditable="true"]')) {event.preventDefault();onToggle();}
      if(event.key==='Escape'){setSelectedIds(new Set());setArmed(null);}
    };
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[onToggle,setArmed]);
  useEffect(()=>{
    setSelectedIds(previous=>{
      const next=new Set([...previous].filter(id=>state.sessions.some(s=>s.id===id)));
      return next.size===previous.size?previous:next;
    });
  },[state.sessions]);
  const rows=useMemo(()=>state.sessions.filter(s=>Boolean(s.archived)===archived&&(!query.trim()||s.title.toLowerCase().includes(query.trim().toLowerCase())||search?.ids.has(s.id))).map(s=>({...s,createdAt:s.created,updatedAt:s.updated})).sort((a,b)=>{
    // Pi's sort semantics: oldest means creation date; dates without values last.
    const date=(value:string)=>{const parsed=Date.parse(value);return Number.isFinite(parsed)?parsed:null;};
    if(sort==='name'){const name=a.title.localeCompare(b.title,undefined,{sensitivity:'base'});if(name)return name;}
    const order=sidebarSortOrder[sort==='name'?'created':sort];
    const av=date(order.sortKey==='updated_at'?a.updatedAt:a.createdAt),bv=date(order.sortKey==='updated_at'?b.updatedAt:b.createdAt);
    const result=av===null?(bv===null?0:1):bv===null?-1:order.sortDirection==='asc'?av-bv:bv-av;
    return result||a.id.localeCompare(b.id);
  }),[state.sessions,search,query,sort,archived]);
  const sectionOrder=(a:Session,b:Session)=>(a.sectionPosition??Number.MAX_SAFE_INTEGER)-(b.sectionPosition??Number.MAX_SAFE_INTEGER)||a.id.localeCompare(b.id);
  const pinnedSessions=getGlobalPinnedSessions(rows,{}, {},archived).sort(sectionOrder);
  const pinnedSection=state.boot?.sections?.find(s=>s.builtin)||{id:'01984de2-8f74-7c91-a3b2-5c5e937cf318',name:'置顶',builtin:true,appearance:null};
  const customSections=state.boot?.sections?.filter(s=>!s.builtin)||[];
  const customIds=new Set(customSections.map(s=>s.id));
  const sectionRows=customSections.map(section=>({section,sessions:rows.filter(s=>!s.pinned&&s.sectionId===section.id).sort(sectionOrder)}));
  const pinnedIds=new Set(pinnedSessions.map(s=>s.id));
  const history=rows.filter(s=>!pinnedIds.has(s.id)&&(!s.sectionId||!customIds.has(s.sectionId)));
  const origins=groupSidebarOrigins(history,grouping);
  const retained=(state.boot?.settings.preferences.sidebar_retained_time_groups||[]) as RetainedTimeGroup[];
  if(!query.trim()&&sort!=='name')for(const item of retained){
    if(item.mode===grouping&&item.origin&&!origins.some(origin=>origin.id===item.origin!.id))origins.push({...item.origin,sessions:[]});
  }
  const timeGroups=(sessions:SessionSummary[],originId='')=>sort==='name'||query.trim()?[{group:'all' as const,sessions}]:[
    ...(sessions.some(s=>s.timeGroupDetached)?[{group:'ungrouped' as const,sessions:sessions.filter(s=>s.timeGroupDetached)}]:[]),
    ...groupSidebarSessionsByTime(sessions.filter(s=>!s.timeGroupDetached),new Date(),retained.filter(item=>item.mode===grouping&&(item.origin?.id||'')===originId).map(item=>item.group)),
  ];
  const groups=timeGroups(history);
  const visibleHistory=grouping==='list'?groups.flatMap(g=>collapsed[g.group]?[]:g.sessions):origins.flatMap(g=>collapsed[g.id]?[]:timeGroups(g.sessions,g.id).flatMap(time=>collapsed[`${g.id}:${time.group}`]?[]:time.sessions));
  const flatSessionOrder=[...(collapsed[`section:${pinnedSection.id}`]?[]:pinnedSessions.map(s=>s.id)),...sectionRows.flatMap(({section,sessions})=>collapsed[`section:${section.id}`]?[]:sessions.map(s=>s.id)),...(collapsed.ordinary?[]:visibleHistory.map(s=>s.id))];

  // Original Sidebar::handleMultiSelectClick: modifier toggle and anchored range.
  const handleMultiSelectClick=(event:MouseEvent,sessionId:string):boolean=>{
    const isMeta=event.metaKey||event.ctrlKey, isShift=event.shiftKey;
    if(!isMeta&&!isShift){if(selectedIds.size>0)setSelectedIds(new Set());lastClickedIdRef.current=sessionId;return false;}
    event.preventDefault();event.stopPropagation();
    if(isMeta&&!isShift){setSelectedIds(prev=>{const next=new Set(prev);if(next.has(sessionId))next.delete(sessionId);else next.add(sessionId);return next;});lastClickedIdRef.current=sessionId;return true;}
    const anchor=lastClickedIdRef.current;
    const startIdx=anchor?flatSessionOrder.indexOf(anchor):-1,endIdx=flatSessionOrder.indexOf(sessionId);
    if(startIdx===-1||endIdx===-1){setSelectedIds(new Set([sessionId]));lastClickedIdRef.current=sessionId;return true;}
    const range=flatSessionOrder.slice(Math.min(startIdx,endIdx),Math.max(startIdx,endIdx)+1);
    setSelectedIds(prev=>new Set([...prev,...range]));return true;
  };
  const moveFocus=(event:KeyboardEvent<HTMLDivElement>)=>{
    if(!(event.target as HTMLElement).matches('.session-select'))return;
    const buttons=[...event.currentTarget.querySelectorAll<HTMLButtonElement>('.session-select')];
    const current=buttons.indexOf(event.target as HTMLButtonElement);
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:event.key==='ArrowDown'?Math.min(current+1,buttons.length-1):event.key==='ArrowUp'?Math.max(0,current-1):-1;
    if(next<0)return;event.preventDefault();buttons[next]?.focus();buttons[next]?.scrollIntoView({block:'nearest'});
  };
  const chosen=state.sessions.filter(s=>selectedIds.has(s.id));
  const deleteKey=`batch:${JSON.stringify([...selectedIds].sort())}`;
  const cannotDelete=chosen.some(s=>store.running(s.id)||state.submitting[s.id]);
  const batchDelete=async()=>{
    if(cannotDelete||deleting||!chosen.length)return;
    if(armed!==deleteKey){setArmed(deleteKey);return;}
    setArmed(null);setDeleting(true);
    try{for(const session of chosen)await store.delete(session.id);setSelectedIds(new Set());}
    catch(error){store.report(error);}finally{setDeleting(false);}
  };
  const move=(id:string,sectionId:string|null,before?:string)=>{hover.hide();if(state.sessions.some(s=>s.id===id))void store.moveToSection(id,sectionId,sectionId?before:undefined).catch(store.report);};
  const renderSessionRows=(sessions:SessionSummary[],sectionId:string|null=null)=>sessions.map(session=><SessionRow key={session.id} store={store} session={session} selected={state.selected===session.id} multiSelected={selectedIds.has(session.id)} onSelect={handleMultiSelectClick} onRename={onRename} onDelete={onDelete} onMove={organization.move} onDropBefore={(id,before)=>move(id,sectionId,before)} onHover={target=>{if(!optionsOpen)hover.show({session,target,temporary:true,space:'本地工作空间'});}} onHidePreview={hover.scheduleHide} onContextSelection={id=>{if(selectedIds.size&&!selectedIds.has(id))setSelectedIds(prev=>new Set([...prev,id]));}}/>);
  const header=(section:typeof ordinarySection,count:number,key:string)=><HistorySectionHeader section={section} count={count} collapsed={Boolean(collapsed[key])} onToggle={()=>{hover.hide();setCollapsed(old=>({...old,[key]:!old[key]}));}} onMenu={event=>{hover.hide();organization.openMenu(section,event);}} onDrop={id=>move(id,section.id||null)}/>;
  const renderTimeGroups=(sessions:SessionSummary[],prefix='',origin:RetainedTimeGroup['origin']=null)=>timeGroups(sessions,origin?.id).map(group=>{
    const key=`${prefix}${group.group}`;
    return <section className="sidebar-session-group" key={key} data-time-group={key}>
      {group.group!=='all'&&group.group!=='ungrouped'&&<button onContextMenu={event=>{hover.hide();organization.openGroupMenu([origin?.label,groupLabels[group.group]].filter(Boolean).join(' · '),group.sessions,event,{mode:grouping,origin,group:group.group});}} onKeyDown={event=>{if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){hover.hide();organization.openGroupMenu([origin?.label,groupLabels[group.group]].filter(Boolean).join(' · '),group.sessions,event,{mode:grouping,origin,group:group.group});}}} className="sidebar-time-group-header sidebar-group-toggle" aria-label={`${collapsed[key]?'展开':'折叠'} ${groupLabels[group.group]}`} aria-expanded={!collapsed[key]} onClick={()=>{hover.hide();setCollapsed(old=>({...old,[key]:!old[key]}));}}><ChevronDown className={collapsed[key]?'collapsed':''} size={12}/>{groupLabels[group.group]}<span>{group.sessions.length}</span></button>}
      {!collapsed[key]&&<div className="sidebar-session-group-body">{group.sessions.length?renderSessionRows(group.sessions):<p className="history-section-empty">暂无会话</p>}</div>}
    </section>;
  });
  return <aside className="sidebar sidebar-surface pi-session-sidebar" aria-label="会话侧栏">
    <div className="sidebar-header sidebar-top"><div className="brand"><AudioLines size={22}/>{expanded&&<strong>Praat<span>声学助手</span></strong>}</div><IconButton label={expanded?'折叠会话侧栏':'展开会话侧栏'} aria-expanded={expanded} onClick={onToggle}>{expanded?<ChevronLeft size={18}/>:<ChevronRight size={18}/>}</IconButton></div>
    <button className="new-session" title="新建会话" aria-label="新建会话" onClick={()=>create(null)}><Plus size={19}/>{expanded&&'新建会话'}</button>
    {expanded&&<label className="session-search"><Search size={15}/><input type="text" aria-label="搜索会话与消息" placeholder="搜索会话与消息" spellCheck={false} autoCorrect="off" autoCapitalize="off" value={query} onChange={event=>{setQuery(event.target.value);hover.hide();setSelectedIds(new Set());setArmed(null);}}/>{query&&<IconButton label="清除搜索" onClick={()=>setQuery('')}><X size={13}/></IconButton>}</label>}
    {expanded&&<div className="sidebar-list-toolbar"><span className="sidebar-list-label">会话</span><div className="sidebar-toolbar-actions"><button className="sidebar-toolbar-button" aria-label="新建分区" onClick={organization.newSection}><FolderPlus size={15}/></button><SidebarOptions store={store} sort={sort} grouping={grouping} open={optionsOpen} onOpenChange={open=>{if(open)hover.hide();setOptionsOpen(open);}}/></div></div>}
    {expanded&&selectedIds.size>0&&<div className="sidebar-selection-bar"><span>已选 {selectedIds.size} 个会话</span><button aria-label={armed===deleteKey?`确认删除 ${chosen.length} 个会话`:'删除所选会话'} disabled={cannotDelete||deleting} title={cannotDelete?'所选记录包含运行中的会话':undefined} onClick={()=>void batchDelete()}><Trash2 size={14}/>{armed===deleteKey?'确认删除':'删除'}</button><IconButton label="取消多选" onClick={()=>{setSelectedIds(new Set());setArmed(null);}}><X size={14}/></IconButton></div>}
    <div className="session-list sidebar-session-groups" onKeyDown={moveFocus} onScroll={()=>{hover.hide();setOptionsOpen(false);}}>{expanded&&<>
      {search?.loading&&<small className="search-status">正在搜索宿主历史…</small>}
      {pinnedSessions.length>0&&<section className="sidebar-pinned-sessions" data-history-section={pinnedSection.id}>{header(pinnedSection,pinnedSessions.length,`section:${pinnedSection.id}`)}{!collapsed[`section:${pinnedSection.id}`]&&<div className="sidebar-session-group-body">{renderSessionRows(pinnedSessions,pinnedSection.id)}</div>}</section>}
      {sectionRows.filter(({sessions})=>!query.trim()||sessions.length).map(({section,sessions})=><section key={section.id} data-history-section={section.id}>{header(section,sessions.length,`section:${section.id}`)}{!collapsed[`section:${section.id}`]&&(sessions.length?<div className="sidebar-session-group-body">{renderSessionRows(sessions,section.id)}</div>:<p className="history-section-empty">暂无会话</p>)}</section>)}
      <section data-history-section="ordinary">{header(ordinarySection,history.length,'ordinary')}{!collapsed.ordinary&&(grouping==='list'?renderTimeGroups(history):origins.map(group=><section className="sidebar-origin-group" key={group.id} data-sidebar-origin={group.id}>
        <button className="sidebar-origin-header sidebar-group-toggle" title={group.detail} aria-label={`${collapsed[group.id]?'展开':'折叠'}${grouping==='project'?'项目':'连接'} ${group.label}`} aria-expanded={!collapsed[group.id]} onClick={()=>{hover.hide();setCollapsed(old=>({...old,[group.id]:!old[group.id]}));}}><ChevronDown className={collapsed[group.id]?'collapsed':''} size={12}/><span className="sidebar-origin-name">{group.label}</span><span>{group.sessions.length}</span></button>
        {!collapsed[group.id]&&renderTimeGroups(group.sessions,`${group.id}:`,{id:group.id,label:group.label,detail:group.detail})}
      </section>))}</section>
      {!rows.length&&<p className="sidebar-session-empty">{query?'没有匹配记录':'暂无会话，创建一个开始探索'}</p>}
    </>}</div>
    <div className="sidebar-bottom"><button className="task-entry" title="后台任务" aria-label="后台任务" onClick={onTasks}><LoaderCircle size={17}/>{expanded&&'后台任务'}<span className="task-count">{tasks.length}</span></button><button className="settings-entry" title="设置" aria-label="打开设置" onClick={onSettings}><Settings size={20}/>{expanded&&'设置'}</button></div>
    {expanded&&<SidebarResizer width={width} maxWidth={maxWidth} onPreview={onPreviewWidth} onCommit={onCommitWidth} onCollapse={onToggle}/>}
    {organization.dialogs}
    {expanded&&hover.card&&<SessionHoverCard store={store} card={hover.card} keepVisible={hover.keepVisible} scheduleHide={hover.scheduleHide} onOpen={()=>{const id=hover.card!.session.id;hover.hide();void store.select(id).catch(store.report);}}/>}
  </aside>;
}
