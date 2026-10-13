// Local renderer for the open Codex thread-section contract; see codex-source/NOTICE.md.
import {useState,type MouseEvent,type KeyboardEvent,type DragEvent} from 'react';
import {Archive,ArchiveRestore,ChevronDown,Folder,FolderPlus,MoreHorizontal,Pencil,Plus,Trash2} from 'lucide-react';
import {ChatStore,useChatState} from './store';
import {readOnly,type HistorySection,type Session,type HistoryGroupAction,type RetainedTimeGroup} from './types';
import {ContextMenu,type ContextMenuState} from './pi/ContextMenu';
import {Modal} from './ui';
import './codex/organization.css';

type Anchor=Pick<ContextMenuState,'point'|'trigger'>;
type Editing={section?:HistorySection;session?:Session};
type TimeGroup={name:string;sessionIds:string[];retention?:RetainedTimeGroup};
type GroupMenuAction=HistoryGroupAction|'clear';
const groupActionLabels:Record<GroupMenuAction,string>={delete:'删除分区及其内容',detach:'仅删除分区',clear:'删除分区内所有内容',archive:'全部归档'};
const PINNED='01984de2-8f74-7c91-a3b2-5c5e937cf318';
export const ordinarySection:HistorySection={id:'',name:'对话历史',appearance:null,builtin:true};

export function useHistoryOrganization(store:ChatStore,archived:boolean,onCreate:(section:string|null)=>void){
  const state=useChatState(store);
  const [moving,setMoving]=useState<{session:Session;anchor:Anchor;back:()=>void}>();
  const [menu,setMenu]=useState<{section:HistorySection;anchor:Anchor}>();
  const [editing,setEditing]=useState<Editing>();
  const [confirm,setConfirm]=useState<{section:HistorySection;kind:'archive'|'restore'|'remove'}>();
  const [groupMenu,setGroupMenu]=useState<TimeGroup & {anchor:Anchor}>();
  const [groupConfirm,setGroupConfirm]=useState<TimeGroup & {action:GroupMenuAction}>();
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [name,setName]=useState('');const [icon,setIcon]=useState('');const [color,setColor]=useState('');
  const edit=(request:Editing)=>{setName(request.section?.name||'');setIcon(request.section?.appearance?.icon||'');setColor(request.section?.appearance?.color||'');setError('');setEditing(request);};
  const action=async(operation:()=>Promise<unknown>,close:()=>void)=>{setBusy(true);setError('');try{await operation();close();}catch(error){setError(error instanceof Error?error.message:String(error));}finally{setBusy(false);}};
  const members=(section:HistorySection,inArchive=archived)=>state.sessions.filter(s=>Boolean(s.archived)===inArchive&&(section.builtin&&section.id===PINNED?Boolean(s.pinned):!s.pinned&&(s.sectionId||'')===section.id));
  const manageGroup=async(group:TimeGroup & {action:GroupMenuAction})=>{
    if(group.sessionIds.length)await store.manageGroup(group.sessionIds,group.action==='clear'?'delete':group.action);
    if(group.retention&&group.action!=='archive'){
      const saved=(store.getSnapshot().boot?.settings.preferences.sidebar_retained_time_groups||[]) as RetainedTimeGroup[];
      const retained=saved.filter(item=>item.mode!==group.retention!.mode||item.origin?.id!==group.retention!.origin?.id||item.group!==group.retention!.group);
      if(group.action==='clear')retained.push(group.retention);
      if(group.action==='clear'||retained.length!==saved.length)await store.save({preferences:{sidebar_retained_time_groups:retained}});
    }
  };
  const groupMenuState:ContextMenuState|null=groupMenu?{...groupMenu.anchor,label:`分组操作 ${groupMenu.name}`,items:(['detach','clear','archive','delete'] as const).map(kind=>({id:`group-${kind}`,label:groupActionLabels[kind],icon:kind==='archive'?<Archive size={14}/>:<Trash2 size={14}/>,danger:kind==='delete'||kind==='clear',separatorBefore:kind==='archive',disabled:state.organizationBusy||busy||(kind!=='detach'&&groupMenu.sessionIds.some(id=>store.running(id)||state.submitting[id])),onSelect:()=>{setError('');setGroupConfirm({...groupMenu,action:kind});}}))}:null;
  const menuState:ContextMenuState|null=menu?{...menu.anchor,label:`分区操作 ${menu.section.name}`,items:[
    {id:'edit-section',label:'编辑分区',icon:<Pencil size={14}/>,disabled:menu.section.builtin||state.organizationBusy,onSelect:()=>edit({section:menu.section})},
    {id:'archive-section',label:archived?'恢复分区会话':'归档分区',icon:archived?<ArchiveRestore size={14}/>:<Archive size={14}/>,disabled:state.organizationBusy||!members(menu.section).length||(!archived&&members(menu.section).some(s=>store.running(s.id)||state.submitting[s.id])),onSelect:()=>{setError('');setConfirm({section:menu.section,kind:archived?'restore':'archive'});}},
    ...(!archived&&members(menu.section,true).length?[{id:'restore-section',label:'恢复分区会话',icon:<ArchiveRestore size={14}/>,disabled:state.organizationBusy,onSelect:()=>{setError('');setConfirm({section:menu.section,kind:'restore'});}}]:[]),
    {id:'new-in-section',label:'新建会话',icon:<Plus size={14}/>,disabled:state.organizationBusy,onSelect:()=>onCreate(menu.section.id||null)},
    {id:'new-section',label:'新建分区',icon:<FolderPlus size={14}/>,disabled:state.organizationBusy,onSelect:()=>edit({})},
    {id:'remove-section',label:'移除分区',icon:<Trash2 size={14}/>,danger:true,separatorBefore:true,disabled:menu.section.builtin||state.organizationBusy,onSelect:()=>{setError('');setConfirm({section:menu.section,kind:'remove'});}},
  ]}:null;
  const movingState:ContextMenuState|null=moving?{...moving.anchor,label:`移动到分区 ${moving.session.title}`,items:[
    {id:'back',label:'返回会话菜单',back:true,onSelect:moving.back},
    ...[ordinarySection,...(state.boot?.sections||[])].map(section=>({id:section.id||'ordinary',label:section.name,icon:(moving.session.sectionId||'')===section.id?<span>✓</span>:<Folder size={14}/>,disabled:state.organizationBusy||(section.id===PINNED&&readOnly(moving.session)),onSelect:()=>{void store.moveToSection(moving.session.id,section.id||null).catch(store.report);}})),
    {id:'create-move',label:'新建分区',icon:<FolderPlus size={14}/>,separatorBefore:true,onSelect:()=>edit({session:moving.session})},
  ]}:null;
  return {
    newSection:()=>edit({}),
    move:(session:Session,anchor:Anchor,back:()=>void)=>setMoving({session,anchor,back}),
    openMenu:(section:HistorySection,event:MouseEvent<HTMLButtonElement>)=>{const target=event.currentTarget,rect=target.getBoundingClientRect();setMenu(old=>old?.section.id===section.id?undefined:{section,anchor:{point:{x:rect.right+4,y:rect.bottom+4},trigger:target}});},
    openGroupMenu:(name:string,sessions:Session[],event:MouseEvent<HTMLButtonElement>|KeyboardEvent<HTMLButtonElement>,retention?:RetainedTimeGroup)=>{event.preventDefault();event.stopPropagation();const target=event.currentTarget,rect=target.getBoundingClientRect();setGroupMenu({name,sessionIds:sessions.map(s=>s.id),retention,anchor:{point:'clientX'in event&&(event.clientX||event.clientY)?{x:event.clientX+4,y:event.clientY+4}:{x:rect.right+4,y:rect.bottom+4},trigger:target}});},
    dialogs:<>
      <ContextMenu state={menuState} onClose={()=>setMenu(undefined)}/><ContextMenu state={movingState} onClose={()=>setMoving(undefined)}/>
      <ContextMenu state={groupMenuState} onClose={()=>setGroupMenu(undefined)}/>
      {groupConfirm&&<Modal title={groupActionLabels[groupConfirm.action]} onClose={()=>{if(!busy)setGroupConfirm(undefined);}}>
        <p>{groupConfirm.action==='detach'?`仅删除“${groupConfirm.name}”分区？其中 ${groupConfirm.sessionIds.length} 个会话会保留，并直接显示在上一级。`:groupConfirm.action==='clear'?`删除“${groupConfirm.name}”中的全部 ${groupConfirm.sessionIds.length} 个会话？删除后无法在本应用中恢复。分区会保留，清空后显示“暂无会话”。`:groupConfirm.action==='delete'?`删除“${groupConfirm.name}”及其中 ${groupConfirm.sessionIds.length} 个会话？删除后无法在本应用中恢复。`:`归档“${groupConfirm.name}”中的全部 ${groupConfirm.sessionIds.length} 个会话？可从“设置 → 已归档会话”恢复。`}</p>
        {error&&<p role="alert">{error}</p>}<div className="actions"><button disabled={busy} onClick={()=>setGroupConfirm(undefined)}>取消</button><button aria-label={`确认${groupActionLabels[groupConfirm.action]}`} className={groupConfirm.action==='delete'||groupConfirm.action==='clear'?'danger':'primary'} disabled={busy||state.organizationBusy} onClick={()=>void action(()=>manageGroup(groupConfirm),()=>setGroupConfirm(undefined))}>{busy?'处理中…':'确认'}</button></div>
      </Modal>}
      {editing&&<Modal title={editing.section?'编辑分区':'新建分区'} onClose={()=>{if(!busy)setEditing(undefined);}}>
        <label className="field"><span>分区名称</span><input aria-label="分区名称" autoFocus maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label>
        <div className="section-appearance-fields"><label className="field"><span>分区图标</span><input aria-label="分区图标" placeholder="例如 🎵" value={icon} maxLength={16} onChange={e=>setIcon(e.target.value)}/></label><label className="field"><span>分区颜色</span><select aria-label="分区颜色" value={color} onChange={e=>setColor(e.target.value)}><option value="">默认</option>{[['#26715f','绿色'],['#3869b6','蓝色'],['#9257af','紫色'],['#b65d31','橙色']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label></div>
        {error&&<p role="alert">{error}</p>}<div className="actions"><button disabled={busy} onClick={()=>setEditing(undefined)}>取消</button><button className="primary" aria-label="保存分区" disabled={busy||!name.trim()} onClick={()=>void action(async()=>{const appearance={icon:icon||null,color:color||null};if(editing.section)await store.updateSection(editing.section.id,name,appearance);else{const section=await store.createSection(name,appearance);if(editing.session)await store.moveToSection(editing.session.id,section.id);}},()=>setEditing(undefined))}>{busy?'保存中…':'保存分区'}</button></div>
      </Modal>}
      {confirm&&<Modal title={confirm.kind==='remove'?'移除分区':confirm.kind==='restore'?'恢复分区会话':'归档分区'} onClose={()=>{if(!busy)setConfirm(undefined);}}><p>{confirm.kind==='remove'?`移除“${confirm.section.name}”后，其中的会话会回到普通历史，聊天记录会保留。`:`${confirm.kind==='restore'?'恢复':'归档'}“${confirm.section.name}”中的 ${members(confirm.section,confirm.kind==='restore').length} 个会话？${confirm.kind==='restore'?'':'归档后可从“设置 → 已归档会话”恢复。'}`}</p>{error&&<p role="alert">{error}</p>}<div className="actions"><button disabled={busy} onClick={()=>setConfirm(undefined)}>取消</button><button aria-label={confirm.kind==='remove'?'确认移除分区':confirm.kind==='restore'?'确认恢复分区':'确认归档分区'} disabled={busy} className={confirm.kind==='remove'?'danger':'primary'} onClick={()=>void action(()=>confirm.kind==='remove'?store.removeSection(confirm.section.id):store.archiveSection(confirm.section.id||null,confirm.kind==='archive'),()=>setConfirm(undefined))}>确认</button></div></Modal>}
    </>
  };
}

export function HistorySectionHeader({section,count,collapsed,onToggle,onMenu,onDrop}: {section:HistorySection;count:number;collapsed:boolean;onToggle:()=>void;onMenu:(event:MouseEvent<HTMLButtonElement>)=>void;onDrop:(id:string)=>void}){
  const [over,setOver]=useState(false);
  const drag=(event:DragEvent)=>{if(Array.from(event.dataTransfer.types).includes('application/x-aipraat-history')){event.preventDefault();event.dataTransfer.dropEffect='move';setOver(true);}};
  return <div className={`history-section-header${over?' is-drop-target':''}`} onDragOver={drag} onDragLeave={()=>setOver(false)} onDrop={event=>{setOver(false);const id=event.dataTransfer.getData('application/x-aipraat-history');if(id){event.preventDefault();event.stopPropagation();onDrop(id);}}}>
    <button className="history-section-toggle" aria-label={`${collapsed?'展开':'折叠'}分区 ${section.name}`} aria-expanded={!collapsed} onClick={onToggle}><ChevronDown className={collapsed?'collapsed':''} size={12}/><span className="history-section-symbol" style={{color:section.appearance?.color||undefined}}>{section.appearance?.icon||<Folder size={13}/>}</span><span className="history-section-name">{section.name}</span><span className="history-section-count">{count}</span></button>
    <button className="history-section-more" aria-label={`分区操作 ${section.name}`} aria-haspopup="menu" onClick={onMenu}><MoreHorizontal size={14}/></button>
  </div>;
}
