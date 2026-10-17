import {act,cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {SessionSidebar} from '../src/pi/sidebar/Sidebar';
import {ChatStore} from '../src/store';
import {createDemoAdapter} from '../src/demo';

afterEach(cleanup);
const mount=async()=>{
  const store=new ChatStore(createDemoAdapter());await store.initialize();
  const props={store,expanded:true,width:272,maxWidth:520,onToggle:()=>{},onPreviewWidth:()=>{},onCommitWidth:()=>{},onTasks:()=>{},onSettings:()=>{},onRename:()=>{},onDelete:()=>{}};
  const view=render(<SessionSidebar {...props}/>);
  return {store,props,...view};
};
const ids=(root:ParentNode=document)=>[...root.querySelectorAll('[data-session-id]')].map(row=>row.getAttribute('data-session-id'));

it('renders the real brand dropdown and explicit panel icons, with search initially hidden',async()=>{
  const {store,props,rerender}=await mount();
  try {
    const header=document.querySelector('.sidebar-brand-row')!;
    expect(header.textContent).toBe('Praat');
    expect(within(header as HTMLElement).getByRole('button',{name:'会话历史选项'}).querySelector('.lucide-chevron-down')).not.toBeNull();
    expect(screen.queryByRole('textbox',{name:'搜索会话与消息'})).toBeNull();
    expect(screen.getByRole('button',{name:'折叠会话侧栏'}).querySelector('.lucide-panel-left-close')).not.toBeNull();
    expect(screen.getByRole('button',{name:'新建会话'}).querySelector('.lucide-square-pen')).not.toBeNull();
    rerender(<SessionSidebar {...props} expanded={false}/>);
    expect(screen.getByRole('button',{name:'展开会话侧栏'}).querySelector('.lucide-panel-left-open')).not.toBeNull();
    expect(screen.queryByRole('button',{name:'会话历史选项'})).toBeNull();
    expect(screen.getByRole('button',{name:'新建会话'})).not.toBeNull();
    expect(screen.getByRole('button',{name:'后台任务'})).not.toBeNull();
    expect(screen.getByRole('button',{name:'打开设置'})).not.toBeNull();
  } finally {store.stop();}
});

it('keeps default, pinned and custom sections as independently collapsible siblings',async()=>{
  const {store}=await mount();
  try {
    let sectionId='';
    await act(async()=>{
      sectionId=(await store.createSection('语音研究')).id;
      await store.moveToSection('demo-history',sectionId);
      await store.pin('demo-1',true);
    });
    const ordinary=document.querySelector('[data-history-section="ordinary"]')!;
    const custom=document.querySelector(`[data-history-section="${sectionId}"]`)!;
    const pinned=document.querySelector('.sidebar-pinned-sessions')!;
    expect(ordinary.parentElement).toBe(custom.parentElement);
    expect(ordinary.parentElement).toBe(pinned.parentElement);
    expect(ids(ordinary)).toEqual(['legacy:fixture']);
    expect(ids(custom)).toEqual(['demo-history']);
    expect(ids(pinned)).toEqual(['demo-1']);
    fireEvent.click(screen.getByRole('button',{name:'折叠分区 会话'}));
    expect(ids(ordinary)).toEqual([]);expect(ids(custom)).toEqual(['demo-history']);expect(ids(pinned)).toEqual(['demo-1']);
    fireEvent.click(screen.getByRole('button',{name:'折叠分区 语音研究'}));
    fireEvent.click(screen.getByRole('button',{name:'展开分区 会话'}));
    expect(ids(ordinary)).toEqual(['legacy:fixture']);expect(ids(custom)).toEqual([]);expect(ids(pinned)).toEqual(['demo-1']);
    const toggle=screen.getByRole('button',{name:'折叠分区 会话'});
    expect([...toggle.children].map(el=>el.classList[0])).toEqual(['history-section-symbol','history-section-name','lucide','history-section-count']);
    expect(within(ordinary as HTMLElement).getByRole('button',{name:'新建分区'})).not.toBeNull();
    expect(within(ordinary as HTMLElement).getByRole('button',{name:'分区操作 会话'})).not.toBeNull();
    await act(async()=>{await store.moveToSection('demo-history',null);});
    expect(ids(ordinary)).toContain('demo-history');
    expect(store.getSnapshot().selected).toBe('demo-1');
  } finally {store.stop();}
});

it('keeps project and time collapse state when the default section is hidden and restored',async()=>{
  const {store}=await mount();
  try {
    expect(screen.getByRole('button',{name:/折叠项目/})).not.toBeNull();
    const time=screen.getByRole('button',{name:'折叠 今天'});
    expect(time.firstElementChild?.className).toBe('sidebar-group-name');
    fireEvent.click(time);
    fireEvent.click(screen.getByRole('button',{name:'折叠分区 会话'}));
    fireEvent.click(screen.getByRole('button',{name:'展开分区 会话'}));
    expect(screen.getByRole('button',{name:'展开 今天'}).getAttribute('aria-expanded')).toBe('false');
    expect(ids()).toEqual([]);
    fireEvent.click(screen.getByRole('button',{name:'展开 今天'}));
    expect(ids()).toHaveLength(3);
    fireEvent.click(screen.getByRole('button',{name:/折叠项目/}));
    expect(ids()).toEqual([]);
    expect(screen.getByRole('button',{name:/展开项目/}).getAttribute('aria-expanded')).toBe('false');
  } finally {store.stop();}
});
