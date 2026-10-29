using System;
using System.IO;
using System.Diagnostics;
using System.Drawing;
using System.Linq;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AIPraat.Setup
{
    public sealed class DependencyRow : Panel
    {
        public readonly TextBox PathBox=new TextBox();
        public readonly Label Status=new Label();
        public readonly OutlineButton Browse=new OutlineButton(), Process;
        readonly Label title=new Label(), feedback=new Label();
        readonly ToolTip tips=new ToolTip();
        public DependencyRow(string caption,string name,bool processing)
        {
            Height=SetupTheme.Px(56);Margin=Padding.Empty;
            title.Text=caption;title.Font=new Font(SetupTheme.Body,FontStyle.Bold);title.ForeColor=SetupTheme.Text;
            Status.Name=name+"Status";Status.Font=SetupTheme.Small;Status.ForeColor=SetupTheme.Muted;Status.AutoEllipsis=true;
            PathBox.Name=name;PathBox.AccessibleName=caption;PathBox.AutoSize=false;
            Browse.Text=name=="Wav2Vec2ModelPath"?"选择目录…":"浏览…";Browse.Name="Browse"+name;Browse.AccessibleName="浏览"+caption;
            feedback.Font=SetupTheme.Small;feedback.ForeColor=SetupTheme.Error;feedback.Name=name+"Feedback";
            Controls.AddRange(new Control[]{title,Status,PathBox,Browse,feedback});
            if(processing){Process=new OutlineButton{Text=name=="PythonPath"?"安装 Python…":"安装 FFmpeg…",Name="Process"+name,Visible=false};Controls.Add(Process);Process.VisibleChanged+=(s,e)=>PerformLayout();}
            PathBox.TextChanged+=(s,e)=>tips.SetToolTip(PathBox,PathBox.Text);
        }
        public void SetStatus(string text,bool ready,bool actionable)
        {Status.Text=text;Status.ForeColor=ready?SetupTheme.Success:SetupTheme.Muted;tips.SetToolTip(Status,text);if(Process!=null)Process.Visible=actionable;}
        public void SetFeedback(string text){text=SetupTheme.Hint(text);if(feedback.Text==text)return;feedback.Text=text;PerformLayout();}
        protected override void OnLayout(LayoutEventArgs e)
        {
            base.OnLayout(e);int left=SetupTheme.Px(158),button=SetupTheme.Px(112),gap=SetupTheme.Px(12);
            title.SetBounds(0,SetupTheme.Px(7),SetupTheme.Px(144),SetupTheme.Px(24));
            Status.SetBounds(0,SetupTheme.Px(31),SetupTheme.Px(144),SetupTheme.Px(20));
            Browse.SetBounds(Math.Max(left+100,Width-button),SetupTheme.Px(8),button,SetupTheme.Px(40));
            PathBox.SetBounds(left,SetupTheme.Px(8),Math.Max(100,Browse.Left-gap-left),SetupTheme.Px(40));
            int extra=0;if(Process!=null&&Process.Visible){Process.SetBounds(left,SetupTheme.Px(56),SetupTheme.Px(156),SetupTheme.Px(36));extra=SetupTheme.Px(44);}
            feedback.Visible=feedback.Text!="";if(feedback.Visible){int top=SetupTheme.Px(56)+extra,height=TextRenderer.MeasureText(feedback.Text,feedback.Font,new Size(Math.Max(100,Width-left),1000),TextFormatFlags.WordBreak).Height;feedback.SetBounds(left,top,Math.Max(100,Width-left),height);extra+=height+SetupTheme.Px(8);}
            int wanted=SetupTheme.Px(56)+extra;if(Height!=wanted)Height=wanted;
        }
        protected override void Dispose(bool disposing){if(disposing)tips.Dispose();base.Dispose(disposing);}
    }
    public sealed class DependencyPanel : UserControl
    {
        readonly TableLayoutPanel layout=SetupTheme.Stack(),tools=SetupTheme.Stack(),resources=SetupTheme.Stack();
        readonly Dictionary<string,DependencyRow> rows=new Dictionary<string,DependencyRow>();
        readonly Dictionary<string,int> versions=new Dictionary<string,int>();
        readonly Dictionary<string,DependencyCandidate> verified=new Dictionary<string,DependencyCandidate>();
        readonly Dictionary<string,string> details=new Dictionary<string,string>();
        readonly Dictionary<Control,LinkLabel> folds=new Dictionary<Control,LinkLabel>();
        readonly Dictionary<Control,Label> foldCounts=new Dictionary<Control,Label>();
        readonly Timer timer=new Timer{Interval=650};
        readonly HashSet<string> pending=new HashSet<string>();
        readonly FlowLayoutPanel toolbar=new FlowLayoutPanel();
        readonly Label summary=new Label(),runtimeStatus=new Label(),runtimeNote=new Label();
        readonly Panel runtime=new Panel();
        readonly OutlineButton runtimeDetails=new OutlineButton{Text="运行库明细…",Name="RuntimeDetails",Enabled=false};
        readonly LinkLabel retryRuntime=new LinkLabel{Text="重新检测",Name="RetryRuntime",Visible=false};
        readonly LinkLabel discoveryResults=new LinkLabel{Text="查看程序与安装包识别结果…",Name="DiscoveryResults",Height=32,Visible=false};
        readonly LinkLabel alignmentDetails=new LinkLabel{Text="查看对齐环境明细…",Name="AlignmentDetails",Height=32,Visible=false};
        readonly bool installMode;
        string programDirectory;
        public string SearchDirectory {set{programDirectory=value;RuntimeDependencies.InstallRoot=value;}}
        public Control DiscoveryActions {get{return toolbar;}}
        readonly List<DependencyCandidate> packages=new List<DependencyCandidate>();
        RuntimeInstallPlan localPlan=new RuntimeInstallPlan();
        bool loaded,filling,busy;
        int pythonVersion,checkedItems;
        PythonProbe pythonProbe;
        FfmpegProbe ffmpegProbe;
        MfaAlignmentReport alignmentReport;
        AlignmentPaths originalAlignment=new AlignmentPaths();
        public event EventHandler Changed;
        public bool Busy {get{return busy;}}
        public string Device {get;private set;}
        // Edited from the alignment detail dialog; written to alignment.mfa.conda_* on save.
        public string MfaCondaExecutable {get;set;}
        public string MfaCondaEnvironment {get;set;}
        public string PythonPath {get{return PathValidation.Clean(rows["python"].PathBox.Text);}}
        public string FfmpegPath {get{return ffmpegProbe!=null&&ffmpegProbe.Ready?ffmpegProbe.Executable:PathValidation.Clean(rows["ffmpeg"].PathBox.Text);}}
        public string LlamaPath {get{return PathValidation.Clean(rows["llama"].PathBox.Text);}}
        public string ModelPath {get{return installMode?PathValidation.Clean(rows["model"].PathBox.Text):"";}}
        public string MmprojPath {get{return PathValidation.Clean(rows["mmproj"].PathBox.Text);}}
        public AlignmentPaths Alignment {get{return new AlignmentPaths{MfaExecutablePath=rows["mfa"].PathBox.Text,Wav2Vec2ModelPath=installMode?rows["wav"].PathBox.Text:originalAlignment.Wav2Vec2ModelPath,MfaAcousticModelPath=originalAlignment.MfaAcousticModelPath,MfaDictionaryPath=originalAlignment.MfaDictionaryPath,MfaCondaExecutable=MfaCondaExecutable,MfaCondaEnvironment=MfaCondaEnvironment};}}
        public string ValidationMessage {get;private set;}
        public bool Ready {get;private set;}
        public DependencyPanel(bool installMode,string programDirectory)
        {
            this.installMode=installMode;SearchDirectory=programDirectory;Device="cpu";Name="DependencyPanel";Dock=DockStyle.Fill;AutoScroll=true;BackColor=Color.White;Font=SetupTheme.Body;ForeColor=SetupTheme.Text;
            Padding=new Padding(24,16,24,16);Controls.Add(layout);
            toolbar.Name="DependencyDiscoveryActions";toolbar.Size=new Size(264,40);toolbar.WrapContents=false;toolbar.Margin=Padding.Empty;
            var automatic=new OutlineButton{Text="自动查找",Name="FindDependencies",Width=100,Height=36,Margin=new Padding(0,0,8,0)};
            var folder=new OutlineButton{Text="从文件夹识别…",Name="IdentifyDependencyFolder",Width=152,Height=36,Margin=Padding.Empty};
            toolbar.Controls.AddRange(new Control[]{automatic,folder});
            folder.Click+=(s,e)=>{using(var dialog=new FolderBrowserDialog{Description="仅识别此文件夹及子目录中的程序、Python 环境和安装包",ShowNewFolderButton=false})if(dialog.ShowDialog(FindForm())==DialogResult.OK)Search(dialog.SelectedPath);};automatic.Click+=(s,e)=>Search(null);
            AddBlock(new Label{Text="必需环境",Font=new Font(Font,FontStyle.Bold),Height=32});
            AddRow(layout,"python","Python 环境","PythonPath",0);
            runtime.Name="RuntimeStatusPanel";runtime.BackColor=SetupTheme.Surface;runtime.Height=72;runtime.Margin=new Padding(158,8,0,16);runtime.BorderStyle=BorderStyle.FixedSingle;
            runtimeStatus.Name="RuntimePathStatus";runtimeStatus.Font=new Font(SetupTheme.Small,FontStyle.Bold);runtimeStatus.ForeColor=SetupTheme.Muted;
            runtimeNote.Name="RuntimeDetectionNote";runtimeNote.Font=SetupTheme.Small;runtimeNote.ForeColor=SetupTheme.Muted;
            retryRuntime.Font=SetupTheme.Small;retryRuntime.LinkColor=SetupTheme.Primary;
            runtime.Controls.AddRange(new Control[]{runtimeStatus,runtimeNote,runtimeDetails,retryRuntime});AddBlock(runtime);runtime.Margin=new Padding(158,8,0,16);
            runtime.Layout+=(s,e)=>LayoutRuntime();runtimeDetails.Click+=(s,e)=>ConfigureRuntime();retryRuntime.LinkClicked+=(s,e)=>Check("python");
            AddRow(layout,"ffmpeg","FFmpeg","FfmpegPath",0);
            if(installMode){AddFold("可选分析工具",tools,"OptionalTools");AddRow(tools,"llama","llama.cpp","LlamaServerPath",0);AddRow(tools,"mfa","MFA","MfaExecutablePath",0);AddAlignmentEntry(tools);AddFold("本地模型与资源 · 可选",resources,"ModelResources");AddRow(resources,"model","语言模型","ModelPath",0);AddRow(resources,"mmproj","视觉投影","MmprojPath",0);AddRow(resources,"wav","wav2vec2 模型","Wav2Vec2ModelPath",0);}
            else {AddFold("可选工具与资源",tools,"OptionalTools");AddRow(tools,"llama","llama.cpp","LlamaServerPath",0);AddRow(tools,"mfa","MFA","MfaExecutablePath",0);AddAlignmentEntry(tools);AddRow(tools,"mmproj","视觉投影","MmprojPath",0);}
            var hint=new Label{Text=installMode?"模型可在安装后通过资源管理添加":"视觉投影与语言模型的配套关系在模型菜单中确认",Font=SetupTheme.Small,ForeColor=SetupTheme.Muted,AutoSize=true,MaximumSize=new Size(650,0),Padding=new Padding(158,8,0,8)};
            SetupTheme.AddRow(installMode?resources:tools,hint);
            summary.Name="DependencySummary";summary.Font=SetupTheme.Small;summary.Height=44;summary.ForeColor=SetupTheme.Muted;AddBlock(summary);summary.Visible=false;
            discoveryResults.Font=SetupTheme.Small;discoveryResults.LinkColor=SetupTheme.Primary;AddBlock(discoveryResults);discoveryResults.LinkClicked+=(s,e)=>ShowDiscoveryResults();
            alignmentDetails.Font=SetupTheme.Small;alignmentDetails.LinkColor=SetupTheme.Primary;alignmentDetails.LinkClicked+=(s,e)=>ShowAlignmentDetails();
            rows["python"].Process.Click+=(s,e)=>ProcessPython();rows["ffmpeg"].Process.Click+=(s,e)=>ProcessFfmpeg();
            timer.Tick+=(s,e)=>{timer.Stop();var work=pending.ToArray();pending.Clear();foreach(string kind in work)Check(kind);};
            Load+=(s,e)=>{loaded=true;StartChecks();};Resize+=(s,e)=>ResizeBlocks();RefreshState();
        }
        void AddBlock(Control control){SetupTheme.AddRow(layout,control);}
        // The alignment-environment entry sits directly under the MFA row: that is where the
        // MFA path is configured, so the check and its repair live together.
        void AddAlignmentEntry(Control parent){SetupTheme.AddRow((TableLayoutPanel)parent,alignmentDetails);}
        void ResizeBlocks(){layout.Width=Math.Max(100,ClientSize.Width-Padding.Horizontal);foreach(var group in folds.Keys)group.MaximumSize=new Size(layout.Width,0);LayoutRuntime();}
        void LayoutRuntime()
        {
            int pad=SetupTheme.Px(12),button=SetupTheme.Px(140),available=Math.Max(100,runtime.Width-button-pad*3);
            runtimeDetails.SetBounds(runtime.Width-button-pad,pad,button,SetupTheme.Px(36));
            runtimeStatus.SetBounds(pad,SetupTheme.Px(8),available,SetupTheme.Px(24));
            int height=Math.Max(SetupTheme.Px(20),TextRenderer.MeasureText(runtimeNote.Text,runtimeNote.Font,new Size(available,1000),TextFormatFlags.WordBreak).Height);
            runtimeNote.SetBounds(pad,SetupTheme.Px(33),available,height);retryRuntime.SetBounds(pad,runtimeNote.Bottom+SetupTheme.Px(6),available,SetupTheme.Px(24));
            int wanted=Math.Max(SetupTheme.Px(64),runtimeNote.Bottom+SetupTheme.Px(10)+(retryRuntime.Visible?SetupTheme.Px(30):0));if(runtime.Height!=wanted)runtime.Height=wanted;
        }
        void AddFold(string title,Control content,string name)
        {
            var header=new Panel{Height=52};header.Paint+=(s,e)=>{using(var pen=new Pen(SetupTheme.Line))e.Graphics.DrawLine(pen,0,SetupTheme.Px(10),header.Width,SetupTheme.Px(10));};
            var toggle=new LinkLabel{Text="▸ "+title,Name=name,LinkColor=SetupTheme.Text,ActiveLinkColor=SetupTheme.Primary,VisitedLinkColor=SetupTheme.Text,LinkBehavior=LinkBehavior.NeverUnderline,Font=new Font(Font,FontStyle.Bold),TabStop=true};
            var count=new Label{ForeColor=SetupTheme.Muted,Font=SetupTheme.Small,TextAlign=ContentAlignment.MiddleRight};
            header.Controls.AddRange(new Control[]{toggle,count});header.Layout+=(s,e)=>{toggle.SetBounds(0,SetupTheme.Px(22),Math.Max(100,header.Width-SetupTheme.Px(150)),SetupTheme.Px(26));count.SetBounds(header.Width-SetupTheme.Px(150),SetupTheme.Px(22),SetupTheme.Px(150),SetupTheme.Px(26));};
            folds[content]=toggle;foldCounts[content]=count;toggle.Tag=title;content.Tag=false;AddBlock(header);AddBlock(content);content.Visible=false;
            toggle.LinkClicked+=(s,e)=>SetFold(content,!(bool)content.Tag);toggle.KeyDown+=(s,e)=>{if(e.KeyCode==Keys.Space){SetFold(content,!(bool)content.Tag);e.Handled=true;}};
        }
        void SetFold(Control content,bool expanded){if((bool)content.Tag==expanded)return;content.Tag=expanded;content.Visible=expanded;folds[content].Text=(expanded?"▾ ":"▸ ")+folds[content].Tag;ResizeBlocks();}
        DependencyRow AddRow(Control parent,string kind,string title,string name,int unused)
        {
            var row=new DependencyRow(title,name,kind=="python"||kind=="ffmpeg");rows[kind]=row;versions[kind]=0;SetupTheme.AddRow((TableLayoutPanel)parent,row);
            row.Browse.Click+=(s,e)=>Browse(kind);row.PathBox.TextChanged+=(s,e)=>PathChanged(kind);row.PathBox.Leave+=(s,e)=>{if(pending.Contains(kind)&&!busy){pending.Remove(kind);Check(kind);}};return row;
        }
        public void Fill(string python,string ffmpeg,string llama,string model,string mmproj,AlignmentPaths alignment,string device)
        {
            filling=true;pythonVersion++;checkedItems=0;pythonProbe=null;ffmpegProbe=null;verified.Clear();packages.Clear();localPlan=new RuntimeInstallPlan();details.Clear();pending.Clear();timer.Stop();
            foreach(string kind in versions.Keys.ToArray())versions[kind]++;
            originalAlignment=alignment??new AlignmentPaths();Device=device=="cuda"?"cuda":"cpu";
            MfaCondaExecutable=originalAlignment.MfaCondaExecutable;MfaCondaEnvironment=originalAlignment.MfaCondaEnvironment;alignmentReport=null;
            rows["python"].PathBox.Text=python??"";rows["ffmpeg"].PathBox.Text=ffmpeg??"";rows["llama"].PathBox.Text=llama??"";if(installMode)rows["model"].PathBox.Text=model??"";rows["mmproj"].PathBox.Text=mmproj??"";rows["mfa"].PathBox.Text=originalAlignment.MfaExecutablePath;if(installMode)rows["wav"].PathBox.Text=originalAlignment.Wav2Vec2ModelPath;
            filling=false;SetFold(tools,rows["llama"].PathBox.Text!=""||rows["mfa"].PathBox.Text!=""||(!installMode&&MmprojPath!=""));if(installMode)SetFold(resources,ModelPath!=""||MmprojPath!=""||originalAlignment.Wav2Vec2ModelPath!="");RefreshState();if(loaded)StartChecks();
        }
        public void StartChecks(){foreach(string kind in new[]{"python","ffmpeg","llama","mfa","model","mmproj","wav"})if(rows.ContainsKey(kind))Check(kind);}
        void PathChanged(string kind)
        {
            if(filling)return;versions[kind]++;verified.Remove(kind);details.Remove(kind);
            if(kind=="python"){pythonVersion++;pythonProbe=null;localPlan.before=null;}
            if(kind=="ffmpeg")ffmpegProbe=null;
            rows[kind].SetStatus("待检测",false,false);pending.Add(kind);timer.Stop();if(loaded&&!busy)timer.Start();RefreshState();
        }
        void Browse(string kind)
        {
            if(busy)return;
            if(kind=="wav") {using(var dialog=new FolderBrowserDialog{Description="选择本地 wav2vec2 模型目录"})if(dialog.ShowDialog(FindForm())==DialogResult.OK){rows[kind].PathBox.Text=dialog.SelectedPath;pending.Remove(kind);Check(kind);}return;}
            string filter=kind=="model"||kind=="mmproj"?"GGUF 模型|*.gguf":kind=="python"?"Python 环境|python.exe":kind=="ffmpeg"?"FFmpeg|ffmpeg.exe":kind=="llama"?"llama.cpp|llama-server.exe":"MFA|mfa.exe;*.bat;*.cmd";
            using(var dialog=new OpenFileDialog{Filter=filter,CheckFileExists=true})if(dialog.ShowDialog(FindForm())==DialogResult.OK){rows[kind].PathBox.Text=dialog.FileName;pending.Remove(kind);Check(kind);}
        }
        async void Check(string kind)
        {
            if(IsDisposed||busy)return;int generation=++versions[kind];string path=PathValidation.Clean(rows[kind].PathBox.Text);rows[kind].SetStatus("检测中…",false,false);
            if(kind=="python") {
                int request=++pythonVersion;checkedItems=0;pythonProbe=null;RefreshState();string device=Device;
                var progress=new Progress<string>(line=>{if(IsDisposed||request!=pythonVersion||generation!=versions[kind])return;if(RuntimeDependencies.Items().Any(item=>line.StartsWith(item.label+": ")))checkedItems++;if(line.StartsWith("正在检测 ")){runtimeNote.Text=line+" · "+checkedItems+"/"+RuntimeDependencies.Items().Length+" 项已返回";LayoutRuntime();}});
                var result=await Task.Run(()=>RuntimeDependencies.Probe(path,device,true,line=>((IProgress<string>)progress).Report(line)));
                if(IsDisposed||request!=pythonVersion||generation!=versions[kind]||device!=Device)return;
                pythonProbe=result;details[kind]=result.Message+(result.Report!=null&&!string.IsNullOrEmpty(result.Report.log_path)?"\r\n日志："+result.Report.log_path:"");
                var program=await Task.Run(()=>DependencyDiscovery.ProbeProgram("python",path));
                if(IsDisposed||request!=pythonVersion||generation!=versions[kind])return;
                if(program!=null)verified[kind]=program;rows[kind].SetStatus(program!=null?program.Version:packages.Any(x=>x.Kind==kind)?"安装包已找到 · 待安装":"缺失或不支持（3.10–3.12 x64）",program!=null,program==null&&packages.Any(x=>x.Kind==kind));
            } else if(kind=="llama") {
                var result=await Task.Run(()=>DependencyDiscovery.ProbeProgram(kind,ResolveCommand(kind,path)));
                if(IsDisposed||generation!=versions[kind])return;
                if(result!=null)verified[kind]=result;
                rows[kind].SetStatus(result!=null?ShortVersion(result.Version):path==""?"未配置":"未验证可用",result!=null,false);details[kind]=result==null?"可选工具未配置或版本检测未通过":result.Description;
            } else if(kind=="mfa") {
                var result=await Task.Run(()=>DependencyDiscovery.ProbeProgram(kind,ResolveCommand(kind,path)));
                if(IsDisposed||generation!=versions[kind])return;
                if(result!=null)verified[kind]=result;
                // The environment report is produced even when MFA is not configured yet:
                // the detail entry is where the conda launcher is chosen, so it must be
                // reachable before the optional tool is set up. The row itself still reads
                // "未配置" and does not nag about a tool the user never opted into.
                alignmentReport=null;
                var python=verified.ContainsKey("python")?PythonPath:"";
                alignmentReport=await Task.Run(()=>RunAlignmentProbe(python));
                if(IsDisposed||generation!=versions[kind])return;
                if(result==null) {
                    rows[kind].SetStatus(path==""?"未配置":"未验证可用",false,false);
                    details[kind]=path==""?"可选工具未配置：填写 MFA 程序或 conda 环境后可检测对齐环境":"MFA 版本检测未通过，无法继续检测对齐环境";
                } else {
                    rows[kind].SetStatus(alignmentReport.ready?"对齐环境可用":"对齐环境不完整",alignmentReport.ready,false);
                    details[kind]=alignmentReport.Describe();
                }
            } else if(kind=="ffmpeg") {
                ffmpegProbe=null;RefreshState();var result=await Task.Run(()=>FfmpegDependencies.Probe(path));
                if(IsDisposed||generation!=versions[kind])return;ffmpegProbe=result;details[kind]=result.Message;
                if(result.Ready)verified[kind]=new DependencyCandidate{Kind=kind,Path=result.Executable,Version=result.Version};
                rows[kind].SetStatus(result.Ready?"实际音频解码通过":packages.Any(x=>x.Kind==kind)?"安装包已找到 · 待安装":"缺失或不可用",result.Ready,!result.Ready);rows[kind].SetFeedback(result.Ready?"":result.Message);
            } else {
                bool exists=kind=="wav"?Directory.Exists(path):File.Exists(path);
                rows[kind].SetStatus(path==""?"未配置":exists?kind=="wav"?"本地模型目录":kind=="model"?"GGUF 模型文件":"GGUF 投影文件":"路径待确认",exists,false);
            }
            RefreshState();
        }
        string ResolveCommand(string kind,string path)
        {
            if(path!=""&&!Path.IsPathRooted(path)&&kind=="mfa") {
                foreach(string directory in (Environment.GetEnvironmentVariable("PATH")??"").Split(Path.PathSeparator))foreach(string name in new[]{path,path+".exe",path+".bat",path+".cmd"}) {
                    try{string candidate=Path.Combine(directory.Trim('"'),name);if(File.Exists(candidate))return candidate;}catch(ArgumentException){}
                }
            }
            return path;
        }
        static string ShortVersion(string version){if(version.StartsWith("ffmpeg version ",StringComparison.OrdinalIgnoreCase)){var parts=version.Split(' ');if(parts.Length>=3)return "FFmpeg "+parts[2];}return version.Length>72?version.Substring(0,72)+"…":version;}
        void RefreshState()
        {
            int count=pythonProbe==null||pythonProbe.Report==null||pythonProbe.Report.items==null?0:pythonProbe.Report.items.Count(x=>x.ok);
            
            runtimeStatus.Text=pythonProbe==null?"正在检测运行库…":pythonProbe.Valid?"运行库已就绪 · "+count+"/"+RuntimeDependencies.Items().Length+" · "+Device.ToUpperInvariant():pythonProbe.Report==null?"检测未完成":"运行库未就绪 · "+count+"/"+RuntimeDependencies.Items().Length;
            runtimeStatus.ForeColor=pythonProbe!=null&&pythonProbe.Valid?SetupTheme.Success:SetupTheme.Muted;
            runtimeNote.Text=pythonProbe==null?"等待所选 Python 环境的实际检测结果":pythonProbe.Valid?"全部必需运行库检测通过"+(pythonProbe.Report!=null&&pythonProbe.Report.log_path==""?" 日志未保存，不影响检测":""):SetupTheme.Hint(pythonProbe.Message);
            runtimeNote.ForeColor=pythonProbe!=null&&!pythonProbe.Valid?SetupTheme.Error:SetupTheme.Muted;
            runtimeDetails.Enabled=verified.ContainsKey("python")&&!busy;retryRuntime.Visible=pythonProbe!=null&&!pythonProbe.Valid&&verified.ContainsKey("python")&&!busy;
            LayoutRuntime();
            foreach(var group in foldCounts.Keys){int configured=rows.Values.Count(row=>row.Parent==group&&PathValidation.Clean(row.PathBox.Text)!="");foldCounts[group].Text=configured==0?"按需配置":"已配置 "+configured+" 项";}
            var optional=new InstallRequest{LlamaServerPath=LlamaPath,ModelPath=installMode?ModelPath:"",MmprojPath=MmprojPath,AlignmentPaths=Alignment,OriginalAlignmentPaths=originalAlignment};
            string error=PathValidation.OptionalPathsError(optional);
            Ready=!busy&&pythonProbe!=null&&pythonProbe.Valid&&ffmpegProbe!=null&&ffmpegProbe.Ready&&error==null;
            ValidationMessage=error??(pythonProbe==null?"正在检测所选 Python 环境…":!pythonProbe.Valid?"请先完成运行库检测":ffmpegProbe==null?"正在检测 FFmpeg…":!ffmpegProbe.Ready?"请配置 FFmpeg 后继续":"必需环境已就绪");
            summary.Text=SetupTheme.Hint(ValidationMessage);summary.ForeColor=Ready?SetupTheme.Success:SetupTheme.Muted;
            summary.Visible=busy;
            string feedbackKind=null;
            if(error!=null)foreach(var match in new[]{new[]{"llama","llama"},new[]{"model","前端模型"},new[]{"mmproj","视觉投影"},new[]{"mfa","MFA"},new[]{"wav","wav2vec2"}})if(rows.ContainsKey(match[0])&&error.IndexOf(match[1],StringComparison.OrdinalIgnoreCase)>=0){feedbackKind=match[0];break;}
            foreach(var pair in rows.Where(pair=>pair.Value.Parent!=layout))pair.Value.SetFeedback(pair.Key==feedbackKind?error:"");
            if(feedbackKind!=null)SetFold(rows[feedbackKind].Parent,true);
            // A working `mfa --version` still says nothing about whether an alignment
            // can run, so the alignment report keeps its own blocking reason visible.
            if(rows.ContainsKey("mfa")&&alignmentReport!=null&&string.IsNullOrEmpty(feedbackKind)) {
                bool configured=PathValidation.Clean(rows["mfa"].PathBox.Text)!=""||PathValidation.Clean(MfaCondaExecutable)!=""||PathValidation.Clean(MfaCondaEnvironment)!="";
                if(configured&&!alignmentReport.ready) {
                    rows["mfa"].SetFeedback("对齐环境不足以运行对齐："+alignmentReport.FirstBlockingReason());
                    SetFold(rows["mfa"].Parent,true);
                } else rows["mfa"].SetFeedback("");
            }
            discoveryResults.Visible=details.ContainsKey("查找结果")&&!busy;
            alignmentDetails.Visible=alignmentReport!=null&&!busy;
            if(Changed!=null)Changed(this,EventArgs.Empty);
        }
        void SetBusy(bool value){busy=value;layout.Enabled=!value;if(value){pythonVersion++;foreach(string key in versions.Keys.ToArray())versions[key]++;timer.Stop();}RefreshState();}
        async void Search(string folder)
        {
            if(busy)return;var configured=new Dictionary<string,string>{{"python",PythonPath},{"ffmpeg",FfmpegPath},{"llama",LlamaPath},{"mfa",rows["mfa"].PathBox.Text}};
            var preserved=new HashSet<string>(verified.Keys);SetBusy(true);summary.Text=folder==null?"正在检查已配置路径、托管目录、PATH 和已注册 Python…":"正在识别所选文件夹及子目录…";
            try {
                var found=await Task.Run(()=>folder==null?DependencyDiscovery.Automatic(configured,programDirectory):DependencyDiscovery.Folder(folder));
                if(IsDisposed)return;
                foreach(var group in found.Candidates.Where(x=>!x.Package&&rows.ContainsKey(x.Kind)).GroupBy(x=>x.Kind)) {
                    var candidates=group.ToArray();var current=await Task.Run(()=>DependencyDiscovery.ProbeProgram(group.Key,configured[group.Key]));
                    if(current!=null||preserved.Contains(group.Key))continue;
                    var selected=candidates.Length==1?candidates[0]:Choose(group.Key,candidates);
                    if(selected!=null)rows[group.Key].PathBox.Text=selected.Path;
                }
                if(folder!=null){packages.Clear();localPlan=new RuntimeInstallPlan();}
                packages.AddRange(found.Candidates.Where(x=>x.Package&&!packages.Any(y=>y.Kind==x.Kind&&y.Path.Equals(x.Path,StringComparison.OrdinalIgnoreCase))));
                foreach(var group in packages.Where(x=>RuntimeDependencies.Items().Any(y=>y.id==x.Kind)&&x.Kind!="webview2").GroupBy(x=>x.Kind)) {
                    var candidates=group.ToArray();var selected=candidates.Length==1?candidates[0]:Choose(group.Key+" 安装包",candidates);if(selected!=null)localPlan.packages[group.Key]=selected.Path;
                }
                var webviews=packages.Where(x=>x.Kind=="webview2").ToArray();
                var webview=webviews.Length==0?null:webviews.Length==1?webviews[0]:Choose("WebView2 安装包",webviews);if(webview!=null)localPlan.webview2=webview.Path;
                if(folder!=null)localPlan.dependency_folder=folder;
                localPlan.dependency_folders=packages.Where(x=>x.Path.EndsWith(".whl",StringComparison.OrdinalIgnoreCase)).Select(x=>Path.GetDirectoryName(x.Path)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                details["查找结果"]=string.Join("\r\n",found.Candidates.Select(x=>(x.Package?"安装包已找到（待安装）：":"已识别：")+x.Version+" · "+x.Path).Concat(found.Notes));
                if(found.Candidates.Count==0)details["查找结果"]="未找到可用程序或安装包，请选择所在文件夹";
                summary.Visible=true;summary.Text=SetupTheme.Hint(details["查找结果"]);
            } catch(Exception error){details["查找结果"]="识别失败："+error.Message;}
            finally {if(!IsDisposed){SetBusy(false);StartChecks();}}
        }
        void ShowDiscoveryResults()
        {
            if(IsDisposed)return;
            using(var form=new Form{Text="程序与安装包识别结果",ClientSize=new Size(800,420),StartPosition=FormStartPosition.CenterParent,Font=Font,BackColor=Color.White}) {
                var results=new TextBox{Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill,Text=details.ContainsKey("查找结果")?details["查找结果"]:""};
                var close=new OutlineButton{Text="关闭",Dock=DockStyle.Bottom,Height=40,DialogResult=DialogResult.OK};form.Controls.Add(results);form.Controls.Add(close);form.AcceptButton=close;form.ShowDialog(FindForm());
            }
        }
        MfaAlignmentReport RunAlignmentProbe(string python)
        {
            try {
                using(var assets=new RuntimeAssets())
                    return MfaAlignmentProbe.Run(python,assets.AlignmentProbe,PathValidation.Clean(rows["mfa"].PathBox.Text),
                        PathValidation.Clean(MfaCondaExecutable),MfaCondaEnvironment.Trim(),originalAlignment.MfaDictionaryPath,
                        originalAlignment.MfaAcousticModelPath,180,null);
            } catch(Exception error) {return new MfaAlignmentReport{Message="对齐环境检测失败："+error.Message};}
        }
        void ShowAlignmentDetails()
        {
            if(IsDisposed||alignmentReport==null)return;
            using(var form=new Form{Text="MFA 对齐环境明细",ClientSize=new Size(900,560),StartPosition=FormStartPosition.CenterParent,Font=Font,BackColor=Color.White}) {
                var list=new TextBox{Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,Dock=DockStyle.Fill,Text=alignmentReport.Describe()};
                var launcher=new Panel{Dock=DockStyle.Bottom,Height=SetupTheme.Px(132),Padding=new Padding(12,8,12,8)};
                var hint=new Label{Text="填写 Conda 路径与环境名，点击“重新检测”；检测后保存路径配置",
                    Font=SetupTheme.Small,ForeColor=SetupTheme.Muted,Dock=DockStyle.Top,Height=SetupTheme.Px(40)};
                var condaLabel=new Label{Text="conda 可执行文件",Font=SetupTheme.Small,AutoSize=true,Location=new Point(12,SetupTheme.Px(46))};
                var conda=new TextBox{Text=MfaCondaExecutable,Name="MfaCondaExecutablePath",Width=SetupTheme.Px(360),Location=new Point(SetupTheme.Px(140),SetupTheme.Px(42))};
                var browse=new OutlineButton{Text="浏览…",Width=SetupTheme.Px(80),Height=SetupTheme.Px(30),Location=new Point(conda.Right+SetupTheme.Px(8),SetupTheme.Px(41))};
                browse.Click+=(s,e)=>{using(var dialog=new OpenFileDialog{Filter="conda|conda.exe;conda.bat;conda.cmd",CheckFileExists=true})if(dialog.ShowDialog(form)==DialogResult.OK)conda.Text=dialog.FileName;};
                var environmentLabel=new Label{Text="conda 环境名",Font=SetupTheme.Small,AutoSize=true,Location=new Point(12,SetupTheme.Px(82))};
                var environment=new TextBox{Text=MfaCondaEnvironment,Name="MfaCondaEnvironmentName",Width=SetupTheme.Px(160),Location=new Point(SetupTheme.Px(140),SetupTheme.Px(78))};
                var apply=new OutlineButton{Text="重新检测",Name="RetryAlignmentEnvironment",Width=SetupTheme.Px(110),Height=SetupTheme.Px(34),Location=new Point(SetupTheme.Px(320),SetupTheme.Px(76)),DialogResult=DialogResult.OK};
                launcher.Controls.AddRange(new Control[]{hint,condaLabel,conda,browse,environmentLabel,environment,apply});
                var close=new OutlineButton{Text="关闭",Dock=DockStyle.Bottom,Height=40,DialogResult=DialogResult.Cancel};
                form.Controls.Add(list);form.Controls.Add(launcher);form.Controls.Add(close);form.CancelButton=close;
                if(form.ShowDialog(FindForm())==DialogResult.OK) {
                    MfaCondaExecutable=conda.Text.Trim();MfaCondaEnvironment=environment.Text.Trim();
                    if(Changed!=null)Changed(this,EventArgs.Empty);
                    Check("mfa");
                }
            }
        }
        DependencyCandidate Choose(string title,DependencyCandidate[] candidates)
        {
            using(var form=new Form{Text="选择 "+title,Width=850,Height=180,StartPosition=FormStartPosition.CenterParent,Font=Font}) {
                var list=new ComboBox{DropDownStyle=ComboBoxStyle.DropDownList,Dock=DockStyle.Top,Height=32};list.Items.AddRange(candidates);list.SelectedIndex=0;form.Controls.Add(list);
                var apply=new Button{Text="使用所选候选",Dock=DockStyle.Bottom,DialogResult=DialogResult.OK};form.Controls.Add(apply);form.AcceptButton=apply;
                return form.ShowDialog(FindForm())==DialogResult.OK?(DependencyCandidate)list.SelectedItem:null;
            }
        }
        void ConfigureRuntime()
        {
            if(busy||!verified.ContainsKey("python"))return;SetBusy(true);
            try {using(var form=new RuntimeDependenciesForm(PythonPath,Device,localPlan)){form.ShowDialog(FindForm());Device=form.Device;pythonProbe=form.Probe;}}
            finally {SetBusy(false);StartChecks();}
        }
        async void ProcessPython()
        {
            if(busy)return;
            var installers=packages.Where(x=>x.Kind=="python").ToArray();
            if(installers.Length==0){Browse("python");return;}
            using(var dialog=new Form{Text="安装 Python 环境",Width=640,Height=190,StartPosition=FormStartPosition.CenterParent,Font=Font}) {
                var existing=new Button{Text="选择已有环境中的 python.exe",Dock=DockStyle.Top,Height=40};
                var install=new Button{Text="运行已找到的 Python 安装包…",Dock=DockStyle.Top,Height=40};
                dialog.Controls.Add(new Label{Text="安装包尚未就绪；安装结束后将重新识别并检测 Python 环境",Dock=DockStyle.Top,Height=45});dialog.Controls.Add(install);dialog.Controls.Add(existing);
                DependencyCandidate chosen=null;existing.Click+=(s,e)=>{dialog.DialogResult=DialogResult.Cancel;Browse("python");};
                install.Click+=(s,e)=>{chosen=Choose("Python 安装包",installers);if(chosen!=null)dialog.DialogResult=DialogResult.OK;};
                if(dialog.ShowDialog(FindForm())!=DialogResult.OK||chosen==null)return;
                SetBusy(true);summary.Text="请在 Python 安装程序中完成安装，随后自动检测…";
                try {
                    await Task.Run(()=>{using(var process=Process.Start(new ProcessStartInfo(chosen.Path){UseShellExecute=true}))process.WaitForExit();});
                }catch(Exception error){details["python"]=error.Message;}
                finally {if(!IsDisposed)SetBusy(false);}
                if(!IsDisposed)Search(null);
            }
        }
        async void ProcessFfmpeg()
        {
            if(busy)return;
            using(var form=new Form{Text="安装 FFmpeg 音频解码",Width=640,Height=220,StartPosition=FormStartPosition.CenterParent,Font=Font}) {
                var online=new Button{Text="联网一键安装",Dock=DockStyle.Top,Height=40};var offline=new Button{Text="使用已找到的 ZIP 安装包…",Dock=DockStyle.Top,Height=40,Enabled=packages.Any(x=>x.Kind=="ffmpeg")};
                form.Controls.Add(new Label{Text="选择本地安装包，或联网安装 FFmpeg",Dock=DockStyle.Top,Height=50});form.Controls.Add(offline);form.Controls.Add(online);
                string archive="",checksum="";bool selected=false;
                online.Click+=(s,e)=>{selected=true;form.DialogResult=DialogResult.OK;};
                offline.Click+=(s,e)=>{var chosen=Choose("FFmpeg ZIP",packages.Where(x=>x.Kind=="ffmpeg").ToArray());if(chosen==null)return;archive=chosen.Path;
                    using(var hash=new Form{Text="填写发布方 SHA-256",Width=640,Height=155,StartPosition=FormStartPosition.CenterParent,Font=Font}){var input=new TextBox{Dock=DockStyle.Top};var ok=new Button{Text="校验并安装",Dock=DockStyle.Bottom,DialogResult=DialogResult.OK};hash.Controls.Add(input);hash.Controls.Add(ok);if(hash.ShowDialog(form)!=DialogResult.OK)return;checksum=input.Text.Trim();}
                    if(!System.Text.RegularExpressions.Regex.IsMatch(checksum,"^[0-9a-fA-F]{64}$")){MessageBox.Show(form,"需要发布方提供的 64 位 SHA-256");return;}selected=true;form.DialogResult=DialogResult.OK;
                };
                if(form.ShowDialog(FindForm())!=DialogResult.OK||!selected)return;SetBusy(true);
                var progress=new Progress<string>(line=>{if(!IsDisposed)summary.Text=line;});
                try {var installed=await Task.Run(()=>FfmpegDependencies.Install(line=>((IProgress<string>)progress).Report(line),archive,checksum));if(IsDisposed)return;details["ffmpeg"]=installed.Message;if(installed.Ready)rows["ffmpeg"].PathBox.Text=installed.Executable;}
                finally {if(!IsDisposed){SetBusy(false);StartChecks();}}
            }
        }
        protected override void Dispose(bool disposing){if(disposing){if(toolbar.Parent==null)toolbar.Dispose();pythonVersion++;timer.Dispose();foreach(string key in versions.Keys.ToArray())versions[key]++;}base.Dispose(disposing);}
    }
}
