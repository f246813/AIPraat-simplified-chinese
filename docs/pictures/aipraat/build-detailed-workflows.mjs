import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const dir=path.dirname(fileURLToPath(import.meta.url));
const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
class Figure {
 constructor(title,subtitle){this.parts=[];this.y=120;this.count=0;this.title=title;this.subtitle=subtitle;this.nodes=new Map();this.omit=new Set();}
 node(x,y,w,id,title,lines,accent=false){
  const h=Math.max(118,76+lines.length*28);
  this.parts.push('<g data-node="'+id+'"><rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="14" fill="'+(accent?'#e0efe8':'#fff')+'" stroke="'+(accent?'#79aa95':'#cddbd4')+'"/><text class="heading" x="'+(x+22)+'" y="'+(y+34)+'">'+esc(id+'  '+title)+'</text>'+lines.map((s,i)=>'<text class="body" x="'+(x+22)+'" y="'+(y+69+i*28)+'">'+esc(s)+'</text>').join('')+'</g>');
  this.count++;const node={x,y,w,h,c:x+w/2,id};this.nodes.set(id,node);return node;
 }
 edge(a,b){if(this.omit.has(a.id+'>'+b.id))return;this.parts.push('<path class="edge" d="M'+a.c+' '+(a.y+a.h)+'V'+b.y+'"/>');}
 section(title,description,steps){
  this.parts.push('<text class="section" x="50" y="'+(this.y+30)+'">'+esc(title)+'</text><text class="note" x="50" y="'+(this.y+61)+'">'+esc(description)+'</text>');this.y+=85;
  let prior;for(const step of steps){const n=this.node(50,this.y,1100,...step);if(prior)this.edge(prior,n);this.y+=n.h+34;prior=n;}this.y+=35;
 }
 pair(leftTitle,rightTitle,left,right){
  this.parts.push('<text class="section" x="50" y="'+(this.y+30)+'">'+esc(leftTitle)+'</text><text class="section" x="650" y="'+(this.y+30)+'">'+esc(rightTitle)+'</text>');this.y+=60;
  const start=this.y;let leftPrev,rightPrev;
  for(let i=0;i<Math.max(left.length,right.length);i++){
   const a=left[i]?this.node(50,this.y,500,...left[i]):null;
   const b=right[i]?this.node(650,this.y,500,...right[i]):null;
   if(a&&leftPrev)this.edge(leftPrev,a);if(b&&rightPrev)this.edge(rightPrev,b);
   if(a)leftPrev=a;if(b)rightPrev=b;
   this.y+=Math.max(a?.h??0,b?.h??0)+34;
  }
  this.y+=35;return {left:leftPrev,right:rightPrev,start};
 }
 route(from,to,lane,fromDelta=0,toDelta=0){const a=this.nodes.get(from),b=this.nodes.get(to);const right=lane>1150;const ax=right?a.x+a.w:a.x,bx=right?b.x+b.w:b.x;const ay=a.y+a.h/2+fromDelta,by=b.y+b.h/2+toDelta;this.parts.push('<path class="edge" d="M'+ax+' '+ay+'H'+lane+'V'+by+'H'+bx+'"/>');}
 note(s){this.parts.push('<text class="note" x="50" y="'+(this.y+22)+'">'+esc(s)+'</text>');this.y+=48;}
 save(name){
  const h=this.y+30;
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="'+h+'" viewBox="0 0 1200 '+h+'" role="img"><title>'+esc(this.title)+'</title><desc>'+esc(this.subtitle)+'</desc><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#52746c"/></marker></defs><style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}.title{font-size:31px;font-weight:700;fill:#173c33}.section{font-size:25px;font-weight:700;fill:#173c33}.heading{font-size:22px;font-weight:700;fill:#173c33}.body{font-size:19px;fill:#405d53}.note{font-size:18px;fill:#536b64}.edge{fill:none;stroke:#52746c;stroke-width:2.2;marker-end:url(#arrow)}</style><rect width="1200" height="'+h+'" rx="20" fill="#f5f8f6"/><text class="title" x="50" y="53">'+esc(this.title)+'</text><text class="note" x="50" y="88">'+esc(this.subtitle)+'</text>'+this.parts.join('\n')+'</svg>\n';
  fs.writeFileSync(path.join(dir,name),svg);console.log(name,this.count+' nodes',h+'px');
 }
}
const n=(id,title,...lines)=>[id,title,lines];
let f=new Figure('A · AI 对话后端逐步调用图','与后端总览配合阅读；各段内部按箭头顺序执行，段间跳转由入口与退出条件决定。');
f.section('提交 → 原目标绑定 → 路由','这一段由桌面宿主执行，模型尚未开始规划。',[
 n('A01','前端提交','React / assistant-ui 收集文本、附件 ID 与继续动作，经 HostAPI RPC 提交到 ModernApplication.submit。'),
 n('A02','读取会话与配置','读取会话历史、已有证据、模型设置和 WorkflowPolicy；解释已有结果的追问可绑定上一轮目标。'),
 n('A03','锁定原目标','捕获 Praat 进程身份、对象 ID / 类型 / 名称与编辑器选区；继续任务使用原来保存的目标。'),
 n('A04','建立任务与材料','创建任务 ID、取消标记和消息占位；校验附件，读取文本或建立原始 WAV 片段快照。'),
 n('A05','准备模型上下文','按 dialogue / planner / report 阶段恢复模型历史；估算上下文用量，必要时生成并持久保存摘要。'),
 n('A06','选择执行入口','明确时长 / 统计查询 → 直接工具快路径；一般对话 → L4；显式继续解释 → L2；原音频 → L3。','其余有 Praat 原目标的工具任务 → L0；本地 Qwen 获取服务租约后进入独立循环。')
]);
f.section('L0 · 规划与专用工具循环','成功、执行结束或不可继续时进入 L2；简单测量已证明完成且无需解释时可直接交付。',[
 n('L0.1','建立规划 Agent','Pydantic AI 注册工具 JSON Schema、ToolGuards Hooks 与按需技能目录；Pydantic Graph 驱动任务阶段。'),
 n('L0.2','发送规划请求','带入原目标对象 / 范围、用户材料和已有成功证据；模型返回原生 function calling 工具名与参数。'),
 n('L0.3','执行前检查与复用','检查参数 schema、对象类型、编号、范围、命名与脚本；同目标同范围的成功测量直接复用。','相同失败调用跳过；提案错误按有限规则反馈模型，不冒充实际测量失败。'),
 n('L0.4','工具分流与脚本调用','内置工具 / custom_script → 当前 GUI Praat；run_praat_script → 导出副本后独立 Praat 批处理。','脚本生成、Message.txt、WM_APP、完成标记与批处理步骤在 B 图逐步展开。'),
 n('L0.5','回读并保存执行事实','读取结果行与失败信息，区分 success / failed / not_executed / unknown；保存 Attempts 与 Evidence。','publish_results → SQLite 落盘 → results / activity 事件；测量结果可先于模型解释展示。'),
 n('L0.6','判断继续还是退出','新增有效证据 → 回 L0.2；可修复执行失败 → L1；完成、预算耗尽或分支不可继续 → L2 / 阶段交付。','连续两轮无新增有效证据停止工具分支；状态不明阻止新投递，等待明确核对。')
]);
f.section('L1 · 对实际执行失败进行有限纠正','这是 L0 的纠正支路；不是每轮必须经过的阶段。',[
 n('L1.1','分类失败','依赖缺失、不支持、材料不可用、执行状态不明等永久原因直接退出；仅可修复失败允许继续。'),
 n('L1.2','反馈失败并请求不同修正','AnalysisState.failed 按工具问题计数；配置允许 0–1 次纠正，ModelRetry 把真实失败原因反馈模型。'),
 n('L1.3','重新进入 L0 的检查与执行','有依据且有区别的新参数 → L0.3 / B 图；纠正成功回 L0，重复失败或次数耗尽 → L2。')
]);
f.section('L2 · 独立的证据解释与报告核对','解释已有结果也可直接从 A06 进入；本阶段没有执行工具。',[
 n('L2.1','发布已经完成的工具结果','先保存并展示实测结果；测量完成且工作流程不需要解释时 finish_locally 直接交付。'),
 n('L2.2','重建报告上下文','带入原始目标、逐项 deliveries、证据编号 / quantity_refs、操作记录、材料来源及未完成项。'),
 n('L2.3','请求结构化解释','模型输出 AnalysisReport：中文分析 + coverage 完成状态 + missing_evidence；默认按需、15 秒、最多 2 次请求。'),
 n('L2.4','报告守卫核对与有限纠正','核对数值、单位、量名、对象、范围和证据引用；有引用的有限算式由程序计算。','纠正仍失败时保留正文并遮蔽未核对数值；没有可用正文时交付阶段记录，不重跑对象操作。'),
 n('L2.5','决定交付或升级原音频','报告确认缺原音频证据，且 auto / 音频能力 / 材料 / 预算条件均满足 → L3；否则进入 A 后处理。')
]);
f.section('L3 · 原目标音频快照、发送与回退','显式请求可直接进入；无测量且已绑定音频时，auto 策略也可进入。',[
 n('L3.1','验证音频分析入口','核对 WorkflowPolicy、audio_input_enabled、原始材料、取消状态与剩余请求预算。'),
 n('L3.2','取得或核验原目标片段','已绑定 WAV → 检查来源、范围和指纹；Praat 对象 → 经 B 图导出原目标临时 PCM WAV。','使用任务已绑定的原目标，不能重新拿后来变化的选区当作原材料。'),
 n('L3.3','读取音频并构建供应商输入','检查音频时长 / 字节数与供应商限制；按实际端点能力构建 BinaryContent / Base64 等音频内容。'),
 n('L3.4','发送、接收并记录音频状态','分别记录 audio_sent、audio_received 与 audio_report_completed；模型定性观察注明模型和材料来源。'),
 n('L3.5','核对解释与处理能力拒绝','实际不支持音频的端点错误可纠正对应配置；材料不可用或能力拒绝时回 L2 使用已有证据。','成功音频报告进入 A 后处理，听感观察不当作精确声学测量。')
]);
f.section('L4 · 一般讨论与快速对话','由一般原理、科普、能力介绍或一般讨论继续动作进入；不从 L3 自动跳到本层。',[
 n('L4.1','准备独立对话上下文','配置允许时建立 dialogue 阶段历史；无需 Praat 测量快照，不声称执行了工具或听过本轮音频。'),
 n('L4.2','请求并流式回传','run_dialogue_turn → 模型响应 → delta 事件；按配置选择思考与输出预算。'),
 n('L4.3','保留实际输出状态','正常完成保存正式对话；长度截断时按现有规则有限续写，失败保留已经收到的正文。')
]);
f.section('本地 Qwen · 独立规划循环','A06 配置为本地模型时进入；没有把它套入云端 API 的阶段图。',[
 n('Q01','获取模型服务租约','宿主按配置和显存启动或复用自己管理的 llama-server；任务结束后按租约与服务策略释放。'),
 n('Q02','准备历史与工具接口','run_turn 带入用户目标、对象上下文和历史证据；明确测量 / VOT 请求可走既有直接执行分支。'),
 n('Q03','模型生成工具提案','按服务能力走原生 function calling 或既有结构化规划接口；解析工具名、参数和动作列表。'),
 n('Q04','调用既有执行器','参数检查 → _Dispatcher → B 图 GUI / 批处理执行；逐步回读真实观察，记录成功与失败。'),
 n('Q05','观察回灌与循环控制','把工具结果反馈模型，按默认 3 轮 / 5 个动作、取消和失败规则决定继续或收尾；后续修改受多步意图限制。'),
 n('Q06','形成回答与保存结果','有实测结果则带来源组织回答；没有完成的任务说明缺口；进入 A07–A10 的持久化与界面交付。')
]);
f.section('A 后处理 · 所有实际结果回到桌面宿主','本地 Qwen 也共用证据与结果交付；默认最多 3 轮 / 5 个动作，使用既有执行器。',[
 n('A07','清理任务临时对象与音频','只清理因本任务测量而创建、并已核实的临时对象；用户明确要求创建 / 保留的对象保留。','关闭 TaskMaterials 清理任务临时音频；用户源文件保留。'),
 n('A08','保存最终记录','保存消息、目标、材料元数据、Evidence、Attempts、结果和阶段模型上下文；区分完成、部分完成、失败、取消。'),
 n('A09','回传界面与继续动作','results / activity / delta 更新对话窗口；提供 L2 继续解释、L3 分析原材料、L4 一般讨论等可用动作。'),
 n('A10','继续时恢复原目标','下一轮恢复原目标、成功证据、执行记录与材料指纹；继续解释不会重放已完成的对象操作。')
]);
f.note('API 跨阶段共享：每轮 12 次模型请求、20 次工具尝试；各阶段和 token 预算另行核对。');
f.save('backend-steps.svg');
fs.copyFileSync(path.join(dir,'backend-steps.svg'),path.join(dir,'backend-workflow.svg'));

f=new Figure('B · 工具、Praat 脚本与结果回读','GUI 工具投递与社区脚本批处理分开；社区脚本导出输入时仍使用左侧 GUI 投递步骤。');
f.section('一次工具提案进入实际执行层','来自 L0 或直接测量快路径；本地模型也通过相同执行适配器。',[
 n('B01','校验与绑定','ToolGuards 归一化参数并核对目标；_Dispatcher 持有原 Praat 身份、ToolContext、取消标记和串行锁。'),
 n('B02','决定脚本路径','内置 Tool / custom_script → 左列；run_praat_script（LocalTool）→ 右列。')
]);
const ends=f.pair('当前 GUI Praat：固定工具 / 自定义脚本','社区 .praat：导出输入 → 独立批处理',[
 n('G01','生成工具脚本','TOOL_MAP 调用对应 build 函数。','measure 读 measures.tsv，建立派生对象和查询。'),
 n('G02','按所选工具进行专用处理','custom_script 校验语法、对象与命名。','Info 输出改写到结果文件；VOT 计算或估计。'),
 n('G03','组装对象选择与结果输出','选中实际对象，带入范围和参数。','输出到结果文件，补 done 状态。'),
 n('G04','重新核对 Praat 身份与快照','PRAAT_LOCK 覆盖核对、渲染、投递和回读。','核对原 PID / 身份；ping 刷新对象列表。'),
 n('G05','建立独立请求脚本','分配 request_id，清理旧结果。','写 commands/chat_command_<id>.praat。'),
 n('G06','投递到指定进程','sendpraat 写 Message.txt。','向原 Praat 窗口发送 WM_APP。'),
 n('G07','Praat 消费并执行消息','请求脚本前缀先消费消息，记录 started ID。','执行原生命令、对象操作与声学测量。'),
 n('G08','原生结束回调与完成等待','结束回调刷新上下文并记录 finished ID。','宿主轮询并核对本请求编号及完成标记。'),
 n('G09','回读结果与投递事实','读取结果行、failure 和实际执行状态。','未送达与已投递但结果不明分别记账。'),
 n('G10','保存事实与必要的后续核对','生成 Evidence / Attempts，回传 activity / results。','状态不明封锁新投递；成功核对 ping 可解除。')
],[
 n('S01','读取脚本与解析 form','确认脚本文件，读取源文本。','解析表单字段，生成其默认值参数。'),
 n('S02','导出 Sound 输入副本','render(save_sound) → G03–G09。','当前 GUI Praat 保存 work/input.wav。'),
 n('S03','可选导出 TextGrid','存在可用 TextGrid 时通过 GUI 脚本导出。','批处理使用副本，不读取 GUI 编辑器选区。'),
 n('S04','生成 wrapper.praat','Read from file 导入输入副本。','恢复对象名称，按字段顺序准备 runScript 参数。'),
 n('S05','记录目标文件夹执行前状态','记录脚本目录文件名与修改时间。','用于执行后报告新增 / 改动的输出文件。'),
 n('S06','启动独立 Praat 批处理','Praat.exe --FULL-TRUST --run wrapper.praat。','工作目录为本次 work；捕获 stdout / stderr。'),
 n('S07','包装脚本调用社区脚本','runScript: 原脚本路径 + form 默认参数。','独立实例只含导出的 Sound / TextGrid 副本。'),
 n('S08','等待退出或结束批处理','轮询进程；取消或超时结束该子进程。','用返回码和真实输出判断运行成功 / 失败。'),
 n('S09','解码输出并比较生成文件','读取脚本打印内容。','对比执行前后目录快照，列出新增 / 改动文件。'),
 n('S10','形成工具观察','返回脚本名称、默认参数、输入副本和输出路径。','进入 L0 证据回传或 L1 / L2 失败处理。')
]);
f.note('custom_script 运行于当前 GUI；run_praat_script 的用户脚本运行于独立批处理，两者有不同调用契约。');
f.note('AI VOT 工具使用 tools/ 的人工地标或声学候选脚本；此工具路径没有 MFA / wav2vec2 调用。');
f.note('MFA / wav2vec2 的实际调用来自对比纠音分析，见 C、D 图。');
f.save('script-workflow.svg');

f=new Figure('C · MFA / wav2vec2 音素对齐逐步图','对应 forced_alignment.py；auto 依次尝试可用后端，双列表示两种实现，不表示并行执行。');
f.section('调用对齐器前的准备','D 图决定哪一份音频需要对齐，并传入目标音位序列和参考文本。',[
 n('C01','构造对齐输入','输入 audio_path、PhoneSpec 音位序列、language、transcript，以及当前 AlignmentConfig。'),
 n('C02','构造候选后端','build_aligner：auto → MFA + wav2vec2；mfa / wav2vec2 / ctc → 对应后端；记录工程置信度参数。'),
 n('C03','CompositeAligner 逐个尝试','跳过不可用后端；调用 align；捕获 AlignmentError 并保留警告；收集成功的 AlignmentResult。')
]);
f.pair('MFA：临时语料 → 子进程 → TextGrid','wav2vec2：模型前向 → CTC 强制对齐',[
 n('M01','检查 MFA 是否可用','enabled + 启动器 + acoustic_model。','使用 mfa 或 conda run -n <环境> mfa。'),
 n('M02','建立临时语料目录','创建 corpus / output。','把待对齐音频复制或转换为 learner.wav。'),
 n('M03','选择文本与发音词典','有 transcript 和词典时写真实 learner.txt。','否则写 UTT，生成 UTT + IPA 序列的小词典。'),
 n('M04','构建 mfa align 命令','corpus、dictionary、acoustic_model、output。','long_textgrid、单说话人、beam / retry_beam。'),
 n('M05','启动并等待 MFA 子进程','subprocess.run 捕获 stdout / stderr。','返回码非零 → AlignmentError，保留原因。'),
 n('M06','查找输出 TextGrid','优先 learner.TextGrid，否则查找候选文件。','没有输出则记录失败。'),
 n('M07','解析 phones 音素层','提取 xmin / xmax / label。','过滤空标签、静音和无效标签。'),
 n('M08','匹配目标音位序列','SequenceMatcher 后检查有序完整匹配。','重复音位必要时按有序子序列匹配；不匹配报错。'),
 n('M09','返回 MFA 音位边界','构造 AlignedPhone：索引、IPA、起止秒数。','附 source=mfa 和工程置信度；临时语料随后清理。')
],[
 n('W01','检查依赖与加载模型','检查 enabled、model、torch、transformers。','AutoProcessor + AutoModelForCTC 加载。','eval，移动到 device。'),
 n('W02','读取待对齐音频','read_wav 解码 PCM 样本并转 float32。','多声道在读取器中取均值。'),
 n('W03','匹配模型采样率','不匹配时按时长生成目标时间轴。','NumPy 插值重采样到配置 sample_rate。'),
 n('W04','Processor 构造模型输入','带 sampling_rate，return_tensors=pt。','input_values 移动到配置 device。'),
 n('W05','模型前向与概率','torch.inference_mode 获取 logits。','log_softmax → CPU NumPy 对数概率矩阵。'),
 n('W06','映射目标音位 token','token_map、IPA / label 与词表匹配。','未知音位 token 报错，不静默删除。'),
 n('W07','CTC 动态规划与回溯','在帧概率与目标 token 序列上强制对齐。','处理 blank / token 转移，回溯每个 token 的帧。'),
 n('W08','把帧转换为时间区间','起帧 / 总帧数 × 音频时长。','末帧后一个边界 / 总帧数 × 时长。'),
 n('W09','返回 wav2vec2 边界','构造 AlignedPhone 与模型来源。','返回 CTC 工程置信度，不作为发音正确概率。')
]);
f.omit=new Set(['C04>C05','C05>C06']);
f.section('成功结果合并或回退','两个后端都可用时收集双方结果；不是无条件先 MFA 失败才运行 wav2vec2。',[
 n('C04','按成功结果数量分流','0 个成功 → C05；1 个成功 → 原样保留结果和警告；多个成功 → C06。'),
 n('C05','没有可用强制对齐：比例定位','ProportionalAligner 按参考边界与目标音频时长缩放，工程置信度 0.25；明确 timing is approximate。','此支路直接到 C08，不把比例时间当成可靠音素边界。'),
 n('C06','选择可比较结果','以工程置信度选 primary，只合并音位数量一致的结果；不足两份则保留 primary 并附警告。'),
 n('C07','融合边界并评估分歧','按置信度加权平均起止时间；由两后端边界分歧计算 agreement，再调整逐音位置信度。','低于配置 minimum_confidence 时附警告，汇总 source=dual 和平均置信度。'),
 n('C08','回传对齐结果','返回索引、IPA、区间、来源、置信度和警告；D 图再检查双方区间、音类适用性与有效测量帧。')
]);
f.route('C04','C05',18,0,-20);f.route('C04','C06',1168,20);f.route('C04','C08',1188,-20);f.route('C05','C08',18,20);{const a=f.nodes.get('C06'),b=f.nodes.get('C08');f.parts.push('<path class="edge" d="M1150 '+(a.y+a.h/2+20)+'H1168V'+(b.y-18)+'H1040V'+b.y+'"/>');}
f.note('C05 为回退支路，直接返回 C08；多后端融合走 C06 → C07 → C08。');
f.save('alignment-workflow.svg');

f=new Figure('D · 标准音对比纠音的完整调用链','独立于 AI 对话的 L0–L4；双方边界已有时可跳过强制对齐，基础报告无需 LLM。');
f.section('Praat 菜单 → Python 桥 → 请求','菜单与 JSON / 自然语言请求最终进入同一个 tutor 分析入口。',[
 n('D01','选择两个 Sound，启动纠音','PraatAiControl_runAnalysis 检查至少两个选中对象；praat_runPythonScriptFile 启动 run_ai_tutor.py。'),
 n('D02','生成桥接输入与打开表单','PraatBridge 读取选中对象及导出的声音文件；表单选择标准音、学习者、语言、IPA、判据与解释选项。'),
 n('D03','形成 AnalysisRequest','也可读取 request JSON，或由 Qwen 将自然语言转换为请求；按对象编号 / 名称解析真实选中 Sound。'),
 n('D04','准备分析配置与可选模型','加载对齐配置，校验 profile_id / language；仅需 Qwen 解释或视觉解释时尝试启动本地模型。')
]);
f.section('双方音素边界的取得','下面的分支都由 pronunciation.py 决定，统一接到 C 图。',[
 n('D05','读取双方 PCM 音频','解码波形，赋予稳定 phone_index；检查双方是否同路径或相同音频样本。'),
 n('D06','决定标准音是否需要对齐','可靠标准音边界已提供 → 使用；同音频且有学习者边界 → 复用其边界。','未提供或来自 ui_equal_estimate → 调用 C 图，所得边界回写为 reference_start / reference_end。'),
 n('D07','决定学习者是否需要对齐','同一音频 → 复用标准音边界；双方学习者边界已提供 → 使用 provided；否则调用 C 图。','保留真实 learner_start / learner_end、来源和工程置信度。')
]);
f.omit=new Set(['D13>D14']);
f.section('切片 → 声学特征 → 音类或旧式曲线比较','C 图只提供时间区间；声学比较与证据筛选在这里执行。',[
 n('D08','按每个音位切片与分帧','extract_phone_tracks 按双方边界切片；默认约 25 ms 帧长、10 ms 帧移，生成带绝对时间的采样点。'),
 n('D09','NumPy 基础特征','Hanning 窗与 FFT；逐帧计算 RMS、过零率、频谱重心和 2 kHz 以上高频占比。'),
 n('D10','可选 Parselmouth 特征','读取 Praat Sound，生成 Pitch、Intensity、Formant Burg；按对应时间点采样 F0、强度、F1 / F2 / F3。','缺失和未定义值保持缺失，不补成可靠数值。'),
 n('D11','检查双方区间与有效证据','排除非有限、空区间、越界、低质量对齐、重叠和缺失特征；记录逐音位证据状态。'),
 n('D12','根据 profile 分流','英语 / 日语新判据 → D13；未设置 profile_id 的旧式请求 → D14；两条分支均汇总到 D15。'),
 n('D13','新音位判据','PanPhon 分解与分类 IPA；按元音、擦音、近音等选择适用特征，比较曲线与相邻音位归一化时长。','保留日语长度 / 无声化 / N / cl；输出 observations / assessments / check_statuses，不给校准总分。'),
 n('D14','旧式声学对比','对双方有效特征曲线做 DTW；计算均值偏差、容差归一化与距离分数，按旧阈值生成候选偏差。','按用户所选路径执行；新判据不经过此步骤。'),
 n('D15','汇总 AnalysisResult','汇总双方音位轨迹、区间、错误或观察、证据状态、对齐来源、警告与 profile_snapshot。')
]);
f.route('D12','D14',1180);f.route('D13','D15',18);
f.section('可选解释 → 报告文件 → Praat 回导','文字解释在写报告前；视觉解释需先生成叠加图；顺序按 tutor.py。',[
 n('D16','可选 Qwen 文字解释','启用且服务可用 → 请求解释实际 AnalysisResult；不可用时记录原因，基础分析结果保留。'),
 n('D17','创建持久报告目录并写文件','写 TextGrid、JSON、PNG；TextGrid 和 PNG 复制到 Praat 临时回导目录，JSON 留在持久目录。'),
 n('D18','可选 Qwen 视觉解释','有 PNG 且启用视觉解释时，发送叠加图与实际结果；追加解释后重写持久 JSON。'),
 n('D19','在 AI 工作台显示报告','交互入口投递到报告会话，展示双方数值、差异、缺测和 PNG；保存正文、附件与测量证据。','命令行或桌面交付失败时输出文件路径；按服务策略结束自己启动的本地模型。'),
 n('D20','原生回导与临时清理','Praat 读取临时目录中的 TextGrid / Photo 为对象，随后清理临时回导目录；持久报告保留。')
]);
f.note('D13 和 D14 是互斥支路：D13 → D15；D14 → D15。图中的阶段顺序不表示两条比较路径都执行。');
f.save('pronunciation-details.svg');

f=new Figure('E · 表驱动测量与 AI VOT 工具','这是 B 图 G01–G03 中内置脚本构造与测量步骤的展开；两列按用户所选工具进入。');
f.omit.add('V02>V03');
f.pair('measure：表驱动的声学查询','vot：显式边界或声学估计',[
 n('E01','解析参数与量名','从 measures.tsv 取得请求参数对应的条目。','专用量单独委派到对应工具。'),
 n('E02','绑定输入与分析设置','核对 Sound 或已有 Pitch / Intensity 等对象。','检查单位、时间范围、基频上下限及量的设置。'),
 n('E03','计算派生对象依赖','_measure_plan 收集所需派生对象。','按表顺序保证依赖先建立，已有分析对象可直接用。'),
 n('E04','生成 To X 原生命令','每次派生前重新 selectObject 正确来源。','保存 Pitch / Formant / Harmonicity 等新对象编号。'),
 n('E05','生成实际查询','按条目选择对象并展开范围、单位和参数。','运行 Get mean / Get value 等对应查询命令。'),
 n('E06','验证观察后派生测量','统计与绝对斜率共用选区筛查帧。','首尾 F0 保留实际时刻，不外推缺测边缘。','不自动判声调类别；保留公式和缺测原因。','已有 Pitch 的提取设置保持未知。'),
 n('E07','清理脚本内派生对象','按模板 Remove 临时分析对象。','通过 _assemble 加入结果文件和 done 状态。'),
 n('E08','保存结构化证据再解释','点事件与持续区间分别导出 TextGrid。','缺测也保留范围、量名和原因，不表示完成。','参数和帧数与 F0 分型；实测值不重新推导。')
],[
 n('V01','判断是否有显式事件时刻','同时给 burst 和 voicing → V02。','仅有一个事件时报错；无事件边界走 V03 估计。'),
 n('V02','显式 VOT 计算支路','核对 Sound / TextGrid 与两个真实时刻。','VOT = voicing − burst；此支路直接到 V07。'),
 n('V03','确定自动估计搜索范围','显式 from / to 优先；否则读取编辑器选区。','给的是搜索范围，不把起止点当作两个事件。'),
 n('V04','处理音频与确定时长','选择真实 Sound，对范围和时长做检查。','脚本配置高频强度检测与起浊搜索参数。'),
 n('V05','估计爆破事件','依据高频能量跃升及局部候选条件找爆破。','保留事件依据与脚本时间分辨率。'),
 n('V06','检查预浊与爆破后周期性','爆破前低频预浊与爆破后稳定起声分别检查。','起点截断时缺测；闭塞归属未确认则保留候选。'),
 n('V07','输出独立地标与有符号 VOT','VOT 支持正／零／负；单列爆破后间隔。','清晰元音仅人工给定；基频不冒充元音起点。'),
 n('V08','核对实际测量是否完成','B 图执行后要求真实 VOT 数值结果行。','验证事件、保存地标与公式，再生成分节报告。')
]);
f.route('V01','V03',1180,20);
f.route('V02','V07',620,-20);
f.note('V02 是显式时刻支路，V03–V06 是自动估计支路；最终经 V07–V08 输出，未调用强制对齐。');
f.save('measurement-workflow.svg');
