import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const directory=path.dirname(fileURLToPath(import.meta.url));
// Transform only the temporary Vite preview. No repository source is written.
export function previewSidebarTransform(source,id){
 const file=id.replaceAll('\\','/');
 if(file.endsWith('/src/main.tsx')){
  source=source.replace('<PronunciationForm/>','<PronunciationForm rpc={previewRouteRpc}/>').replace('<AnkiIntegration/>','<AnkiIntegration rpc={previewRouteRpc}/>').replace('<SpeechDictionaries/>','<SpeechDictionaries rpc={previewRouteRpc}/>');
  return source+'\n'+fs.readFileSync(path.join(directory,'preview-window-fixtures.ts'),'utf8');
 }
 if(file.endsWith('/src/pi/sidebar/Sidebar.tsx')){
  source=source.replace('Settings, Trash2, X','Settings, SquarePen, PanelLeftClose, PanelLeftOpen, Trash2, X');
  source=source.replace("const [query,setQuery]=useState('');","const [query,setQuery]=useState('');\n  const [previewSearchOpen,setPreviewSearchOpen]=useState(false);");
  const start=source.indexOf('    <div className="sidebar-header sidebar-top">');
  const end=source.indexOf('    {expanded&&selectedIds.size>0',start);
  if(start<0||end<0)throw new Error('Preview sidebar source no longer matches');
  source=source.slice(0,start)+fs.readFileSync(path.join(directory,'preview-sidebar-top.tsx'),'utf8')+'\n'+source.slice(end);
  source=source.replace("{header(ordinarySection,history.length,'ordinary')}","{header(ordinarySection,history.length,'ordinary')}<button className=\"preview-section-create sidebar-toolbar-button\" aria-label=\"新建分区\" onClick={organization.newSection}><FolderPlus size={14}/></button>");
  return source;
 }
 if(file.endsWith('/src/HistoryOrganization.tsx'))return source.replace("name:'对话历史'","name:'会话'");
 if(file.endsWith('/src/SidebarOptions.tsx'))return source.replace('{Check,ChevronRight,MoreHorizontal}','{Check,ChevronDown,ChevronRight,MoreHorizontal}').replace('<MoreHorizontal size={15}/>','<ChevronDown size={12}/>');
 if(file.endsWith('/src/Chat.tsx')){
  source=source.replace('FileText, LoaderCircle','FileText, FolderClosed, LoaderCircle');
  source=source.replace('<h1>{session.title', '<h1><FolderClosed aria-hidden="true" size={14}/><span className="preview-title-text">{session.title');
  source=source.replace("|| '新会话'}{readOnly(session)","|| '新会话'}</span>{readOnly(session)");
  const start=source.indexOf('<p><span className={`connection-dot');
  if(start<0)throw new Error('Preview chat header source no longer matches');
  const end=source.indexOf('</p>',start)+4;
  return source.slice(0,start)+source.slice(end);
 }
 if(file.endsWith('/src/Composer.tsx')){
  source=source.replace('useEffect, useRef, useState','useEffect, useLayoutEffect, useRef, useState');
  source=source.replace('  editorRef.current = editor;', '  const previewWide = usePreviewComposerLayout(editor, form);\n  editorRef.current = editor;');
  source=source.replace('className="composer-zone"','className={`composer-zone ${previewWide ? "preview-composer-wide" : ""}`}');
  return source+'\n'+fs.readFileSync(path.join(directory,'preview-composer-layout.ts'),'utf8');
 }
 return null;
}
