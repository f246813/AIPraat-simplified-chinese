// Codex public archive/delete/list protocols attached to the existing Pi menu,
// Codex search adapter and Praat host. Layout follows the user's Codex screenshot;
// the public repository does not publish this desktop settings React renderer.
import {useEffect,useMemo,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import * as Menu from '@radix-ui/react-dropdown-menu';
import {Check,ChevronDown,Folder,FolderOpen,ListFilter,MoreHorizontal,Search,Trash2,X} from 'lucide-react';
import type {ChatStore} from '../store';
import {type HistorySection,type Session} from '../types';
import {IconButton,Modal} from '../ui';
import {ContextMenu,type ContextMenuState} from '../pi/ContextMenu';
import type {ThreadUnarchiveParams} from './protocol/ThreadUnarchiveParams';
import type {ThreadDeleteParams} from './protocol/ThreadDeleteParams';
import type {ThreadListParams} from './protocol/ThreadListParams';
import {useSessionSearch} from './useSessionSearch';
import {archiveSource,archiveSources,archivedGroups,type ArchivedGroup} from './archived-groups';
import './sidebar-options.css';
import './archived-sessions.css';

const dates=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'});
// Same installed Radix RadioGroup composition as SidebarOptions; no new menu engine.
function ArchiveFilter({label,value,options,onChange,icon}:{label:string;value:string;options:{value:string;label:string}[];onChange:(value:string)=>void;icon:React.ReactNode}){
  return <Menu.Root modal={false}><Menu.Trigger asChild><button className="archived-filter" aria-label={label}>{icon}<span>{options.find(option=>option.value===value)?.label||options[0].label}</span><ChevronDown size={15}/></button></Menu.Trigger>
    <Menu.Portal><Menu.Content className="context-menu is-open sidebar-options-menu archived-filter-menu" aria-label={label} sideOffset={5} collisionPadding={8}>
      <Menu.RadioGroup value={value} onValueChange={onChange}>{options.map(option=><Menu.RadioItem key={option.value} value={option.value} className="context-menu-item sidebar-options-choice"><Menu.ItemIndicator className="sidebar-options-check"><Check size={14}/></Menu.ItemIndicator>{option.label}</Menu.RadioItem>)}</Menu.RadioGroup>
    </Menu.Content></Menu.Portal>
  </Menu.Root>;
}
type Deletion={ids:string[];scope:'one'|'group'|'all';name:string};
export function ArchivedSessions({store,sessions,busy,onOpen,sections=store.getSnapshot().boot?.sections||[],headerActions}:{store:ChatStore;sessions:readonly Session[];sections?:readonly HistorySection[];busy:boolean;onOpen:(id:string)=>void;headerActions?:HTMLElement|null}){
  const rows=useMemo(()=>sessions.filter(s=>s.archived).sort((a,b)=>(Date.parse(b.updated)||0)-(Date.parse(a.updated)||0)||a.id.localeCompare(b.id)),[sessions]);
  const allGroups=useMemo(()=>archivedGroups(rows,sections),[rows,sections]);
  const [query,setQuery]=useState('');
  const [source,setSource]=useState('all');const [project,setProject]=useState('all');
  const listParams:Pick<ThreadListParams,'archived'|'sourceKinds'>={archived:true,sourceKinds:archiveSources.find(option=>option.value===source)?.value==='all'?null:[archiveSources.find(option=>option.value===source)!.value as ReturnType<typeof archiveSource>]};
  const search=useSessionSearch(store,query,Boolean(listParams.archived),sessions);
  const term=query.trim().toLowerCase();
  const groups=allGroups.filter(group=>project==='all'||group.id===project).map(group=>({...group,sessions:group.sessions.filter(session=>(!listParams.sourceKinds||listParams.sourceKinds.includes(archiveSource(session)))&&(!term||session.title.toLowerCase().includes(term)||search?.ids.has(session.id)))})).filter(group=>group.sessions.length);
  const [pending,setPending]=useState('');const [error,setError]=useState('');
  const [menu,setMenu]=useState<ContextMenuState|null>(null);const [deletion,setDeletion]=useState<Deletion>();
  const list=useRef<HTMLDivElement>(null);const focusAfterRestore=useRef<number|null>(null);
  const locked=busy||Boolean(pending);
  const canDelete=(session:Session)=>!store.running(session.id)&&!store.getSnapshot().submitting[session.id];
  useEffect(()=>{
    if(locked||focusAfterRestore.current===null)return;
    const buttons=[...list.current!.querySelectorAll<HTMLButtonElement>('.archived-session-restore')];
    (buttons[Math.min(focusAfterRestore.current,buttons.length-1)]||list.current)?.focus();focusAfterRestore.current=null;
  },[locked,rows]);
  async function restore(params:ThreadUnarchiveParams){
    if(locked)return;
    const buttons=[...list.current!.querySelectorAll<HTMLButtonElement>('.archived-session-restore')];
    const index=buttons.findIndex(button=>button.dataset.threadId===params.threadId),returnFocus=buttons[index]===document.activeElement;
    setPending(params.threadId);setError('');
    try{await store.archive(params.threadId,false);if(returnFocus)focusAfterRestore.current=index;}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setPending('');}
  }
  async function restoreGroup(group:ArchivedGroup){
    if(locked)return;setPending(group.id);setError('');
    try{for(const session of group.sessions)await store.archive(session.id,false);focusAfterRestore.current=0;}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setPending('');}
  }
  function requestDelete(ids:string[],scope:Deletion['scope'],name:string){if(locked)return;setError('');setDeletion({ids,scope,name});}
  async function remove(){
    if(!deletion||locked)return;
    const current=store.getSnapshot().sessions.filter(s=>s.archived&&deletion.ids.includes(s.id));
    setPending('delete');setError('');let deleted=0;
    try{
      if(current.some(session=>!canDelete(session)))throw new Error('所选会话有任务尚未结束，暂时不能删除');
      for(const session of current){const params:ThreadDeleteParams={threadId:session.id};await store.delete(params.threadId);deleted++;}
      setDeletion(undefined);focusAfterRestore.current=0;
    }catch(reason){setError(`${deleted?`已删除 ${deleted} 个会话`:''}${reason instanceof Error?reason.message:String(reason)}`);}
    finally{setPending('');}
  }
  function openGroupMenu(group:ArchivedGroup,trigger:HTMLButtonElement){
    const rect=trigger.getBoundingClientRect();
    setMenu({label:`归档分组操作 ${group.name}`,trigger,point:{x:rect.right,y:rect.bottom+4},items:[
      {id:'restore-group',label:'取消归档此分组',icon:<FolderOpen size={14}/>,disabled:locked,onSelect:()=>void restoreGroup(group)},
      {id:'delete-group',label:'删除分组中的聊天',icon:<Trash2 size={14}/>,danger:true,separatorBefore:true,disabled:locked||group.sessions.some(session=>!canDelete(session)),onSelect:()=>requestDelete(group.sessions.map(s=>s.id),'group',group.name)},
    ]});
  }
  const deleteAll=<button className="archived-delete-all" aria-label="全部删除" disabled={locked||!rows.length||rows.some(session=>!canDelete(session))} onClick={()=>requestDelete(rows.map(s=>s.id),'all','')}><Trash2 size={15}/>全部删除</button>;
  const selectedDelete=sessions.filter(s=>s.archived&&deletion?.ids.includes(s.id));
  return <div className="archived-sessions" ref={list} tabIndex={-1} aria-label="已归档会话列表" onScroll={()=>setMenu(null)}>
    {headerActions?createPortal(deleteAll,headerActions):<div className="archived-inline-actions">{deleteAll}</div>}
    <div className="archived-toolbar"><label className="archived-search"><Search size={17}/><input type="text" aria-label="搜索已归档的聊天" placeholder="搜索已归档的聊天" maxLength={1024} spellCheck={false} autoCorrect="off" autoCapitalize="off" value={query} onChange={event=>{setQuery(event.target.value);setMenu(null);}}/>{query&&<IconButton label="清除归档搜索" onClick={()=>setQuery('')}><X size={14}/></IconButton>}</label>
      <ArchiveFilter label="聊天分类" value={source} options={archiveSources} onChange={setSource} icon={<ListFilter size={17}/>}/>
      <ArchiveFilter label="项目分类" value={project} options={[{value:'all',label:'所有项目'},...allGroups.map(group=>({value:group.id,label:group.name}))]} onChange={setProject} icon={<Folder size={17}/>}/>
    </div>
    {error&&!deletion&&<p role="alert" className="warning">{error}</p>}
    {search?.loading&&<small role="status" className="archived-search-status">正在搜索已归档会话…</small>}
    {groups.map(group=><section className="archived-group" key={group.id} data-archived-group={group.id}>
      <header className="archived-group-header"><Folder size={17} style={{color:group.appearance?.color||undefined}}/><h2 className="archived-group-name" title={group.detail}>{group.appearance?.icon&&<span>{group.appearance.icon}</span>}{group.name}</h2><span className="archived-group-count">{group.sessions.length} 个聊天</span><button className="archived-group-more" aria-label={`归档分组操作 ${group.name}`} aria-haspopup="menu" disabled={locked} onClick={event=>openGroupMenu(group,event.currentTarget)}><MoreHorizontal size={17}/></button></header>
      <div className="archived-group-list">{group.sessions.map(session=>{
        const timestamp=Date.parse(session.updated);
        return <article className="archived-session-row" key={session.id} data-archived-session-id={session.id}>
          <div className="archived-session-details"><button className="archived-session-title" aria-label={`查看已归档会话 ${session.title}`} title={session.title} disabled={locked} onClick={()=>onOpen(session.id)}>{session.title}</button><time className="archived-session-meta" dateTime={Number.isFinite(timestamp)?session.updated:undefined}>{Number.isFinite(timestamp)?dates.format(timestamp):'日期未知'}</time></div>
          <button className="archived-session-delete" aria-label={`删除已归档会话 ${session.title}`} title="删除" disabled={locked||!canDelete(session)} onClick={()=>requestDelete([session.id],'one',session.title)}><Trash2 size={17}/></button>
          <button className="archived-session-restore" data-thread-id={session.id} aria-label={`取消归档 ${session.title}`} disabled={locked} onClick={()=>void restore({threadId:session.id})}>{pending===session.id?'正在恢复…':'取消归档'}</button>
        </article>;
      })}</div>
    </section>)}
    {!groups.length&&!search?.loading&&<p className="archived-sessions-empty">{rows.length?'没有匹配的已归档会话':'暂无已归档会话'}</p>}
    <ContextMenu state={menu} onClose={()=>setMenu(null)}/>
    {deletion&&<Modal title={deletion.scope==='all'?'删除全部已归档会话':deletion.scope==='group'?'删除分组中的已归档会话':'删除已归档会话'} onClose={()=>{if(!locked)setDeletion(undefined);}}>
      <p>{deletion.scope==='one'?`删除“${deletion.name}”？`:deletion.scope==='group'?`删除“${deletion.name}”中当前显示的 ${selectedDelete.length} 个已归档会话？`:`删除全部 ${selectedDelete.length} 个已归档会话？搜索和分类筛选不会限制此操作`}删除后无法在本应用中恢复</p>
      {selectedDelete.some(s=>s.id.startsWith('legacy:'))&&<p className="muted">旧版会话仅从本应用中移除，原始旧版记录会保留</p>}
      {error&&<p role="alert" className="warning">{error}</p>}
      <div className="actions"><button disabled={locked} onClick={()=>setDeletion(undefined)}>取消</button><button className="danger" aria-label="确认删除归档会话" disabled={locked} onClick={()=>void remove()}>{pending==='delete'?'删除中…':'删除'}</button></div>
    </Modal>}
  </div>;
}
