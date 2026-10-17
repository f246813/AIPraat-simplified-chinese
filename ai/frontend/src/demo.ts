import { workflowPresets } from './WorkflowSettings';
// Explicit browser-only development/test fixtures. Never selected as a host fallback.
import { activeTask, type Attachment, type Bootstrap, type HistorySection, type HostEvent, type Message, type Methods, type Rpc, type Session, type Task } from './types';
export function createDemoAdapter(): Rpc {
  if (window.pywebview) throw new Error('测试适配器不能在桌面宿主中运行');
  const now = new Date().toISOString(); let seq = 0; let nextId = 0;
  const compactProcess = new URLSearchParams(window.location.search).get('compactProcess') === '1';
  const sessions: Session[] = [{id:'demo-1',title:'新的分析',created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null},{id:'legacy:fixture',title:'演示旧记录（只读）',created:now,updated:now,readOnly:true,draft:'',scroll:0,anchor:null},{id:'demo-history',title:'长历史与渲染夹具',created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null}];
  const messages: Record<string,Message[]> = {
    'demo-1': [],
    'legacy:fixture': [{id:'legacy-user',role:'user',content:'这是一条只读旧记录（测试夹具）'},{id:'legacy-answer',role:'assistant',content:'旧记录只展示，不恢复 Praat 操作。',status:'complete'}],
    'demo-history': Array.from({length:120},(_,i) => ({id:`history-${i}`,role:i%2 ? 'assistant' : 'user',content:i%2 ? `## 第 ${i+1} 条夹具\n\n这不是实际测量结果。公式：$f_0 = 1/T$。\n\n| 来源 | 证据 |\n|---|---|\n| 演示 | 未执行 |\n\n\`\`\`python\nprint(\"仅离线渲染测试\")\n\`\`\`` : `演示历史问题 ${i+1}`,status:'complete'} as Message)),
  };
  messages['demo-history'].push({id:'diagram-fixture',role:'assistant',content:'```mermaid\nflowchart LR\n A[材料] --> B[宿主校验]\n B --> C[结构化证据]\n```',status:'complete',activities:[{id:'fixture-thinking',type:'thinking',text:'仅测试夹具提供的思考文本，不是真实模型思考。',status:'complete'},{id:'fixture-tool',type:'tool',name:'Praat 测试卡片',args:{object:'fixture',scope:[0,1]},result:{source:'fixture',measured:false},status:'failed',execution:'未投递（演示夹具）'}]});
  // Mid-history collapsible rows. Without content above the held row there is nothing to
  // compensate, so the transcript-scroll fixtures need rows that are not the tail: one to
  // change height above, one to hold as the reading anchor.
  messages['demo-history'][39].activities = [{id:'fixture-early-thinking',type:'thinking',text:'较早折叠夹具：位于锚定行上方，用来制造标题上方的高度变化。\n\n第二段让折叠高度差足够明显。',status:'complete'}];
  messages['demo-history'][59].activities = [{id:'fixture-mid-thinking',type:'thinking',text:'中段折叠夹具：用于验证展开锚定与长历史导航，不是真实模型思考。\n\n第二段让折叠高度差足够明显，便于断言读数位置是否被保持。',status:'complete'}];
  if (new URLSearchParams(window.location.search).get('workflowResults') === '1') messages['demo-1'] = [{id:'workflow-result',role:'assistant',taskId:'workflow-fixture',status:'cancelled',content:'已取消后续解释。离线展示夹具。',result_content:'fixture 总时长 = 1.250000 秒',results:[{id:'fixture-result',tool:'duration',arguments:{object:1},rows:['fixture 总时长 = 1.250000 秒'],status:'success',execution:'delivered',source:'离线工具结果夹具',evidence_ref:0,range:{},reason:'夹具'}]}];
  const tasks = new Map<string,Task>(); const events: HostEvent[] = []; const attachments = new Map<string, Attachment>(); const previews = new Map<string,string>();
  const pinnedId='01984de2-8f74-7c91-a3b2-5c5e937cf318';
  const sections:HistorySection[]=[{id:pinnedId,name:'置顶',appearance:null,builtin:true}];
  const organization=()=>structuredClone({sessions,sections});
  const requireSection=(id:string|null)=>{if(id!==null&&!sections.some(s=>s.id===id))throw new Error('分区不存在');};
  const sectionName=(name:unknown)=>{if(typeof name!=='string'||!name.trim()||name.trim().length>100)throw new Error('分区名称须为 1–100 个字符');return name.trim();};
  const move=(session:Session,id:string|null,before?:string)=>{
    requireSection(id);if(id===pinnedId&&session.readOnly)throw new Error('旧记录不能置顶');
    const order=sessions.filter(s=>s.sectionId===id&&s.id!==session.id).sort((a,b)=>(a.sectionPosition||0)-(b.sectionPosition||0));
    if(before&&(id===null||!order.some(s=>s.id===before)))throw new Error('前置会话不在目标分区');
    session.sectionId=id;session.pinned=id===pinnedId;session.sectionPosition=null;
    if(id!==null){order.splice(before?order.findIndex(s=>s.id===before):order.length,0,session);order.forEach((s,i)=>s.sectionPosition=(i+1)*1000000);}
  };
  const archive=(members:Session[],archived:unknown)=>{
    if(typeof archived!=='boolean')throw new Error('归档状态必须是布尔值');
    if(archived&&members.some(s=>[...tasks.values()].some(t=>t.sessionId===s.id&&activeTask(t))))throw new Error('运行中的会话不能归档');
    members.forEach(s=>s.archived=archived);return organization();
  };
  const boot: Bootstrap = {sessions,settings:{workflow:{...workflowPresets.balanced},api:{enabled:false,locked:false,label:'',base_url:'',model:'',has_api_key:true,request_timeout_sec:120,stop_local_service:true,use_world_knowledge:true,audio_input_enabled:false,token_mode:'auto'},local:{base_url:'http://127.0.0.1:8000/v1',model:'browser-fixture',token_mode:'manual',max_context_tokens:32768,plan_max_tokens:4096},preferences:{theme:'system',font_size:15,send_key:'enter',smooth_stream:true,sidebar_width:272,conversation_width:850},analysis:{default_error_threshold:1.25,maximum_errors_per_phone:3},alignment:{backend:'auto',agreement_threshold_sec:0.04,minimum_confidence:0.45,mfa:{enabled:false,executable:'mfa',beam:10,retry_beam:40}}},tasks:[],host:{name:'浏览器测试夹具',version:'1',cloudAllowed:false},providers:[],models:[]};
  const emit = (task: Task, type: HostEvent['type'], payload: unknown) => events.push({seq:++seq,sessionId:task.sessionId,taskId:task.id,type,payload:structuredClone(payload)});
  return (async (method: keyof Methods, params: any) => {
    if (window.pywebview) throw new Error('测试适配器已禁用：检测到桌面宿主，请移除 demo 参数并刷新');
    const session = sessions.find(s => s.id === params.sessionId);
    switch (method) {
      case 'bootstrap': return structuredClone({...boot,sections,tasks:[...tasks.values()]});
      case 'sessions.search': {
        const needle=String(params.searchTerm).trim().toLowerCase();
        return {sessionIds:needle?sessions.filter(s=>Boolean(s.archived)===Boolean(params.archived)&&(s.title.toLowerCase().includes(needle)||(messages[s.id]||[]).some(m=>m.content.toLowerCase().includes(needle)))).map(s=>s.id):[]};
      }
      case 'sessions.get': if (!session) throw new Error('会话不存在'); return structuredClone({session,messages:messages[session.id]});
      case 'sessions.create': { const destination=params.sectionId??null;requireSection(destination);const s: Session = {id:`demo-new-${++nextId}`,title:params.title || '新会话',created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null,archived:false}; sessions.unshift(s); messages[s.id]=[];move(s,destination);return structuredClone(s); }
      case 'sessions.rename': if (!session || session.readOnly) throw new Error('记录只读'); session.title=params.title; return {ok:true};
      case 'sessions.pin': {
        if (!session || session.readOnly || typeof params.pinned !== 'boolean') throw new Error('记录只读或参数无效');
        if(Boolean(session.pinned)!==params.pinned)move(session,params.pinned?pinnedId:null);
        sessions.sort((a,b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || b.updated.localeCompare(a.updated));
        return structuredClone(session);
      }
      case 'sessions.section': if(!session||!('sectionId'in params))throw new Error('会话或目标分区不存在');move(session,params.sectionId,params.beforeSessionId);return organization();
      case 'sections.create': {const section:HistorySection={id:`demo-section-${++nextId}`,name:sectionName(params.name),appearance:params.appearance??null};sections.push(section);return structuredClone(section);}
      case 'sections.update': {requireSection(params.sectionId);const section=sections.find(s=>s.id===params.sectionId)!;if(section.builtin)throw new Error('内置置顶分区不能编辑');section.name=sectionName(params.name);if('appearance'in params)section.appearance=params.appearance;return structuredClone(section);}
      case 'sections.delete': {requireSection(params.sectionId);if(params.sectionId===pinnedId)throw new Error('内置置顶分区不能移除');sessions.filter(s=>s.sectionId===params.sectionId).forEach(s=>move(s,null));sections.splice(sections.findIndex(s=>s.id===params.sectionId),1);return organization();}
      case 'sessions.archive': if(!session)throw new Error('会话不存在');return archive([session],params.archived);
      case 'sessions.group': {
        const members:Session[]=params.sessionIds.map((id:string)=>{const value=sessions.find(s=>s.id===id);if(!value)throw new Error('会话不存在');return value;});
        if(params.action==='archive')return archive(members,true);
        if(params.action==='delete'&&members.some(s=>[...tasks.values()].some(t=>t.sessionId===s.id&&activeTask(t))))throw new Error('分组中有运行会话，不能删除');
        for(const item of members){
          if(params.action==='detach')item.timeGroupDetached=true;
          else if(params.action==='delete'){sessions.splice(sessions.indexOf(item),1);delete messages[item.id];}
          else throw new Error('分组操作无效');
        }
        return organization();
      }
      case 'sections.archive': requireSection(params.sectionId);return archive(sessions.filter(s=>(s.sectionId??null)===params.sectionId),params.archived);
      case 'sessions.fork': {
        if(!session)throw new Error('会话不存在');const s:Session={id:`demo-fork-${++nextId}`,title:(session.title+' · 分叉').slice(0,100),created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null,archived:false,forkedFrom:session.id,forkedFromTitle:session.title};
        messages[s.id]=structuredClone(messages[session.id]).map(message=>{message.id=`fork-message-${++nextId}`;delete message.taskId;if(message.status==='running')message.status='interrupted';message.activities?.forEach(a=>{a.id=`fork-activity-${++nextId}`;if(a.status==='running')a.status='interrupted';});return message;});
        sessions.unshift(s);move(s,session.sectionId===pinnedId?null:session.sectionId??null);return structuredClone(s);
      }
      case 'settings.choose_model': return {path:'D:\\models\\browser-fixture.gguf'};
      case 'sessions.delete': if (!session || [...tasks.values()].some(t => t.sessionId === session.id && activeTask(t))) throw new Error('会话不能删除'); sessions.splice(sessions.indexOf(session),1); delete messages[session.id]; return {ok:true};
      case 'window.drag': { (window as any).__dshWindowDrags=((window as any).__dshWindowDrags||0)+1; return {ok:false, reason:'browser-fixture'}; }
      case 'sessions.view': if (session) { if (params.draft !== undefined) session.draft=params.draft; if (params.scroll !== undefined) session.scroll=params.scroll; if (params.anchor !== undefined) { if (!params.anchor || typeof params.anchor.messageId !== 'string' || !Number.isFinite(params.anchor.offset)) throw new Error('阅读锚点无效'); session.anchor={messageId:params.anchor.messageId,offset:Number(params.anchor.offset)}; } } return {ok:true};
      case 'sessions.context': {
        if (!session) throw new Error('会话不存在');
        const config = boot.settings.api.enabled ? boot.settings.api : boot.settings.local;
        const overheadTokens = 1200;
        const content = messages[session.id].filter(m => m.status !== 'running').map(m => m.content).join('') + params.text + (params.attachmentIds || []).map((id: string) => previews.get(id) || '').join('');
        const inputTokens = overheadTokens + Math.ceil(content.length / 2);
        const contextWindow = config.token_mode === 'manual' ? Number(config.max_context_tokens) || null : null;
        const reservedTokens = contextWindow ? Number(config.plan_max_tokens) || 4096 : null;
        return {sessionId: session.id, model: String(config.model), estimated: true, inputTokens, overheadTokens, contextWindow, reservedTokens, availableTokens: contextWindow ? contextWindow - inputTokens - reservedTokens! : null, percent: contextWindow ? inputTokens / contextWindow * 100 : null, tokenMode: String(config.token_mode), windowSource: contextWindow ? 'configured' : 'unknown', reason: '显式浏览器演示夹具，非真实宿主用量', exclusions: ['Praat 对象与范围', '音频／图片编码', '正在生成的正文']};
      }
      case 'tasks.submit': {
        if (!session || session.readOnly || session.archived) throw new Error('只读或已归档记录不能提交');
        if ([...tasks.values()].some(t => t.sessionId === session.id && activeTask(t))) throw new Error('本会话正在运行');
        const task: Task = {id:`demo-task-${++nextId}`,sessionId:session.id,status:'running',model:'browser-fixture',created:now}; tasks.set(task.id,task); emit(task,'task',task);
        const user: Message = {id:`${task.id}-user`,role:'user',content:params.text,taskId:task.id,attachments:(params.attachmentIds || []).map((id:string) => attachments.get(id)).filter(Boolean)};
        const answer: Message = {id:`${task.id}-assistant`,role:'assistant',content:'',taskId:task.id,status:'running'};
        if (compactProcess) answer.activities = [{id:'stream-thinking',type:'thinking',text:'离线过程折叠测试。',status:'complete'}];
        messages[session.id].push(user,answer); emit(task,'message',user); emit(task,'message',answer);
        const response = '这是一条**浏览器测试响应**，没有调用模型、云端或 Praat。\n\n可以切换会话观察事件隔离，使用停止按钮取消当前夹具任务。\n\n- Markdown 与公式 $f_0 = 1/T$\n- 附件只有演示元数据\n- 所有数字都不是专业测量结果';
        let offset=0;
        const timer = setInterval(() => {
          if (!activeTask(task)) { clearInterval(timer); return; }
          const chunk=response.slice(offset,offset+5); offset+=5; answer.content+=chunk; emit(task,'delta',{messageId:answer.id,text:chunk});
          if (compactProcess) {
            answer.activities!.push({id:`stream-step-${offset}`,type:'tool',name:`离线测试工具 ${offset}`,args:{fixture:true},result:{measured:false},status:'success',execution:'未投递（演示夹具）'});
            emit(task,'activity',{messageId:answer.id,activity:answer.activities![answer.activities!.length-1]});
          }
          if (offset >= response.length) { clearInterval(timer); task.status='complete'; answer.status='complete'; emit(task,'message',answer); emit(task,'task',task); }
        },130);
        return structuredClone(task);
      }
      case 'tasks.cancel': { const t=tasks.get(params.taskId); if (t) { t.status='cancelled'; const m=messages[t.sessionId].find(m => m.taskId===t.id && m.role==='assistant'); if(m){m.status='cancelled';emit(t,'message',m);} emit(t,'task',t); } return {ok:true}; }
      case 'events.poll': return {events:structuredClone(events.filter(e => e.seq > params.after)),cursor:seq};
      case 'settings.get': return structuredClone(boot.settings);
      case 'settings.save': {
        for (const section of ['api','local','preferences','analysis','alignment','workflow'] as const) {
          if (params.settings[section]) boot.settings[section] = {...boot.settings[section], ...structuredClone(params.settings[section])};
        }
        boot.settings.api.api_key=''; boot.settings.local.api_key=''; return structuredClone(boot.settings);
      }
      case 'settings.test': return {status:'demo-not-verified',reason:`${params.kind === 'audio' ? '音频' : '文字'}测试仅返回显式夹具；没有网络请求，不能视为验证通过。`,details:{adapter:'browser-test',realRequest:false}};
      case 'attachments.choose': { const a={id:`demo-file-${++nextId}`,name:'演示材料.txt',mime:'text/plain',size:24}; attachments.set(a.id,a); previews.set(a.id,'这是演示选择器夹具，不是真实文件。'); return [a]; }
      case 'attachments.import': { const a={id:`demo-file-${++nextId}`,name:params.name,mime:params.mime,size:Math.floor(params.data.length*3/4)}; attachments.set(a.id,a); if(params.mime.startsWith('text/')) previews.set(a.id,new TextDecoder().decode(Uint8Array.from(atob(params.data),(s:string)=>s.charCodeAt(0)))); return a; }
      case 'attachments.preview': {const a=attachments.get(params.attachmentId);if(!a) throw new Error('附件不存在');return {...a,text:previews.get(a.id)};}
      case 'links.open': throw new Error('浏览器测试适配器不打开外部链接');
      default: throw new Error(`测试适配器不支持 ${method}`);
    }
  }) as Rpc;
}
