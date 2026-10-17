// Codex desktop menu hierarchy connected to the existing Praat session store.
// Menu mechanics reuse installed Radix DropdownMenu 2.1.24 (MIT), its official
// Sub/Portal/RadioGroup composition, and existing Pi context-menu styles.
// Public openai/codex supplies sorting types, not desktop React menu source.
import * as Menu from '@radix-ui/react-dropdown-menu';
import {Check,ChevronDown,ChevronRight} from 'lucide-react';
import type {ChatStore} from './store';
import type {ThreadSortKey} from './codex/protocol/ThreadSortKey';
import type {SortDirection} from './codex/protocol/SortDirection';
import './codex/sidebar-options.css';

export const sidebarSorts={recent:'最近更新',oldest:'最早创建',name:'按名称',created:'最近创建'};
export type SidebarSort=keyof typeof sidebarSorts;
export const sidebarSortOrder:Record<Exclude<SidebarSort,'name'>,{sortKey:ThreadSortKey;sortDirection:SortDirection}>={recent:{sortKey:'updated_at',sortDirection:'desc'},oldest:{sortKey:'created_at',sortDirection:'asc'},created:{sortKey:'created_at',sortDirection:'desc'}};
export type SidebarGrouping='project'|'connection'|'list';
const groups={project:'按项目',connection:'按远程连接',list:'在一个列表中'};
const surface='context-menu is-open sidebar-options-menu';

export function SidebarOptions({store,sort,grouping,open,onOpenChange}:{store:ChatStore;sort:SidebarSort;grouping:SidebarGrouping;open:boolean;onOpenChange:(open:boolean)=>void}){
  const save=(preference:string,value:string)=>{void store.save({preferences:{[preference]:value}}).catch(store.report);};
  return <Menu.Root modal={false} open={open} onOpenChange={onOpenChange}>
    <Menu.Trigger asChild><button type="button" className="sidebar-toolbar-button sidebar-options-trigger" aria-label="会话历史选项" title="会话历史选项"><ChevronDown aria-hidden size={12}/></button></Menu.Trigger>
    <Menu.Portal><Menu.Content className={surface} aria-label="会话历史选项" align="start" sideOffset={5} collisionPadding={8}>
      <Menu.Sub>
        <Menu.SubTrigger className="context-menu-item">整理侧边栏<ChevronRight size={14} className="sidebar-options-chevron" aria-hidden/></Menu.SubTrigger>
        <Menu.Portal><Menu.SubContent className={surface} aria-label="整理侧边栏" sideOffset={4} collisionPadding={8}>
          <Menu.RadioGroup value={grouping} onValueChange={value=>save('sidebar_grouping',value)}>
            {Object.entries(groups).map(([value,label])=><Menu.RadioItem key={value} value={value} className="context-menu-item sidebar-options-choice"><Menu.ItemIndicator className="sidebar-options-check"><Check size={14}/></Menu.ItemIndicator>{label}</Menu.RadioItem>)}
          </Menu.RadioGroup>
        </Menu.SubContent></Menu.Portal>
      </Menu.Sub>
      <Menu.Sub>
        <Menu.SubTrigger className="context-menu-item">聊天排序方式<ChevronRight size={14} className="sidebar-options-chevron" aria-hidden/></Menu.SubTrigger>
        <Menu.Portal><Menu.SubContent className={surface} aria-label="聊天排序方式" sideOffset={4} collisionPadding={8}>
          <Menu.RadioGroup value={sort} onValueChange={value=>save('sidebar_sort',value)}>
            {Object.entries(sidebarSorts).map(([value,label])=><Menu.RadioItem key={value} value={value} className="context-menu-item sidebar-options-choice"><Menu.ItemIndicator className="sidebar-options-check"><Check size={14}/></Menu.ItemIndicator>{label}</Menu.RadioItem>)}
          </Menu.RadioGroup>
        </Menu.SubContent></Menu.Portal>
      </Menu.Sub>
    </Menu.Content></Menu.Portal>
  </Menu.Root>;
}
