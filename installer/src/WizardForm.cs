using System;
using System.IO;
using System.Drawing;
using System.Diagnostics;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Reflection;

namespace AIPraat.Setup
{
    public sealed class WizardStepLabel : Label
    {
        public bool Current,Complete;
        public int Number;
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(BackColor);Color color=Complete?SetupTheme.Success:Current?SetupTheme.Primary:SetupTheme.Muted;
            int size=SetupTheme.Px(24),y=(Height-size)/2-SetupTheme.Px(2);var circle=new Rectangle(0,y,size,size);
            using(var pen=new Pen(color)){if(Current)using(var brush=new SolidBrush(color))e.Graphics.FillEllipse(brush,circle);e.Graphics.DrawEllipse(pen,circle);}
            if(Complete)using(var pen=new Pen(color,SetupTheme.Px(2)))e.Graphics.DrawLines(pen,new[]{new Point(SetupTheme.Px(6),y+SetupTheme.Px(12)),new Point(SetupTheme.Px(10),y+SetupTheme.Px(16)),new Point(SetupTheme.Px(18),y+SetupTheme.Px(7))});
            else TextRenderer.DrawText(e.Graphics,Number.ToString(),Font,circle,Current?Color.White:color,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter);
            TextRenderer.DrawText(e.Graphics,Text,Font,new Rectangle(size+SetupTheme.Px(10),0,Width-size-SetupTheme.Px(12),Height-SetupTheme.Px(4)),color,TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
            if(Current)using(var pen=new Pen(SetupTheme.Primary,SetupTheme.Px(3)))e.Graphics.DrawLine(pen,0,Height-2,Width,Height-2);
        }
    }
    public sealed class WizardForm : Form
    {
        readonly InstallRequest request;
        readonly Func<Stream> payloadFactory;
        readonly Panel[] pages=new Panel[3];
        readonly WizardStepLabel[] steps=new WizardStepLabel[3];
        readonly OutlineButton next=new OutlineButton(),previous=new OutlineButton(),cancel=new OutlineButton(),elevate=new OutlineButton();
        readonly TextBox directory=new TextBox();
        DependencyPanel dependencies;
        readonly Label directoryNote=new Label(),prerequisiteNote=new Label(),progressText=new Label(),completionText=new Label(),subtitle=new Label(),completionDescription=new Label(),percent=new Label(),installationError=new Label(),snapshot=new Label();
        readonly Label[] phaseStates=new Label[4];
        readonly SetupProgressBar bar=new SetupProgressBar();
        readonly Panel errorPanel=new Panel();
        readonly FlowLayoutPanel footerActions=new FlowLayoutPanel();
        readonly Timer progressTimer=new Timer{Interval=40};
        readonly CheckBox desktop=new CheckBox();
        int step;
        bool installing,finished,failure,requireElevation,previousLoaded;
        AlignmentPaths originalAlignmentPaths;
        volatile InstallProgress latest;
        public WizardForm(InstallRequest defaults,Func<Stream> payload)
        {
            request=defaults;payloadFactory=payload;Text="AIPraat 安装向导";Name="AIPraatInstaller";AccessibleName=Text;
            Font=SetupTheme.Body;ForeColor=SetupTheme.Text;BackColor=Color.White;ClientSize=new Size(900,780);MinimumSize=new Size(820,650);StartPosition=FormStartPosition.CenterScreen;AutoScaleMode=AutoScaleMode.Dpi;AutoScaleDimensions=new SizeF(96,96);MaximizeBox=false;
            var layout=new TableLayoutPanel{Dock=DockStyle.Fill,RowCount=3,ColumnCount=1,Margin=Padding.Empty};layout.RowStyles.Add(new RowStyle(SizeType.Absolute,136));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,76));Controls.Add(layout);
            var header=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty};layout.Controls.Add(header,0,0);SetupTheme.Divider(header);
            var title=LabelAt(header,"AIPraat 安装向导",24,16,540,32);title.Font=new Font(Font.FontFamily,16.5f,FontStyle.Bold);
            subtitle.Font=SetupTheme.Small;subtitle.ForeColor=SetupTheme.Muted;subtitle.SetBounds(24,52,550,24);header.Controls.Add(subtitle);
            for(int i=0;i<3;i++){steps[i]=new WizardStepLabel{Text=new[]{"选择目录","检查环境","安装程序"}[i],Number=i+1,Name="InstallerStep"+(i+1),Font=SetupTheme.Small,BackColor=Color.White};header.Controls.Add(steps[i]);}
            header.Layout+=(s,e)=>{int left=SetupTheme.Px(24),width=(header.Width-left*2)/3;for(int i=0;i<3;i++)steps[i].SetBounds(left+i*width,SetupTheme.Px(82),width,SetupTheme.Px(52));};
            var content=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty};layout.Controls.Add(content,0,1);
            for(int i=0;i<3;i++){pages[i]=new Panel{Dock=DockStyle.Fill,AutoScroll=i!=1};content.Controls.Add(pages[i]);}
            BuildDirectoryPage();BuildPrerequisitePage();BuildProgressPage();
            var discovery=dependencies.DiscoveryActions;discovery.Location=new Point(612,24);discovery.Anchor=AnchorStyles.Top|AnchorStyles.Right;header.Controls.Add(discovery);
            var footer=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty,Padding=new Padding(24,1,24,0)};layout.Controls.Add(footer,0,2);footer.Paint+=(s,e)=>{using(var pen=new Pen(SetupTheme.Line))e.Graphics.DrawLine(pen,0,0,footer.Width,0);};
            prerequisiteNote.Dock=DockStyle.Fill;prerequisiteNote.Font=SetupTheme.Small;prerequisiteNote.TextAlign=ContentAlignment.MiddleLeft;prerequisiteNote.Name="InstallerValidationMessage";footer.Controls.Add(prerequisiteNote);
            footerActions.Dock=DockStyle.Right;footerActions.WrapContents=false;footerActions.FlowDirection=FlowDirection.RightToLeft;footerActions.Padding=new Padding(0,18,0,0);footer.Controls.Add(footerActions);
            next.Text="下一步";next.Name="Next";next.Primary=true;previous.Text="上一步";previous.Name="Previous";cancel.Text="取消";cancel.Name="Cancel";cancel.DialogResult=DialogResult.Cancel;elevate.Text="以管理员权限重试";elevate.Name="RetryAsAdministrator";elevate.Visible=false;
            foreach(var button in new[]{next,elevate,previous,cancel}){button.Height=40;button.Width=button==elevate?184:button==previous?148:button==next?112:88;button.Margin=new Padding(8,0,0,0);footerActions.Controls.Add(button);}
            AcceptButton=next;CancelButton=cancel;next.Click+=(s,e)=>Next();previous.Click+=(s,e)=>ShowStep(failure&&requireElevation?0:Math.Max(0,step-1));cancel.Click+=(s,e)=>Close();elevate.Click+=(s,e)=>Elevate();
            progressTimer.Tick+=(s,e)=>ApplyProgress();FormClosing+=(s,e)=>{if(installing||dependencies.Busy)e.Cancel=true;};FormClosed+=(s,e)=>progressTimer.Dispose();
            directory.Text=defaults.InstallDirectory;desktop.Checked=defaults.CreateDesktopShortcut;Shown+=(s,e)=>SetupTheme.FitToScreen(this);ShowStep(0);
        }
        static Label LabelAt(Control parent,string text,int x,int y,int width,int height){var label=new Label{Text=text,Location=new Point(x,y),Size=new Size(width,height)};parent.Controls.Add(label);return label;}
        void BuildDirectoryPage()
        {
            var page=pages[0];var title=LabelAt(page,"安装位置",24,20,810,28);title.Font=new Font(Font,FontStyle.Bold);
            LabelAt(page,"选择 AIPraat 的安装文件夹，也可以直接输入完整路径",24,56,810,28).ForeColor=SetupTheme.Muted;
            LabelAt(page,"安装目录",24,100,800,24);
            directory.SetBounds(24,132,716,40);directory.AutoSize=false;directory.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;directory.Name="InstallDirectory";directory.AccessibleName="安装目录";page.Controls.Add(directory);
            var browse=new OutlineButton{Text="浏览…",Name="BrowseInstallDirectory",Location=new Point(752,132),Size=new Size(112,40),Anchor=AnchorStyles.Top|AnchorStyles.Right};page.Controls.Add(browse);
            browse.Click+=(s,e)=>{using(var dialog=new FolderBrowserDialog{Description="选择 AIPraat 的安装文件夹",ShowNewFolderButton=true}){if(Directory.Exists(PathValidation.Clean(directory.Text)))dialog.SelectedPath=PathValidation.Clean(directory.Text);if(dialog.ShowDialog(this)==DialogResult.OK)directory.Text=dialog.SelectedPath;}};
            directoryNote.SetBounds(24,184,816,48);directoryNote.Font=SetupTheme.Small;directoryNote.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;page.Controls.Add(directoryNote);directory.TextChanged+=(s,e)=>RefreshNext();
            desktop.Text="创建桌面快捷方式";desktop.Location=new Point(24,246);desktop.Size=new Size(400,28);desktop.Name="CreateDesktopShortcut";page.Controls.Add(desktop);
            var info=new Panel{Location=new Point(24,298),Size=new Size(840,190),Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right};page.Controls.Add(info);info.Paint+=(s,e)=>{using(var pen=new Pen(SetupTheme.Line))e.Graphics.DrawLine(pen,0,0,info.Width,0);};
            LabelAt(info,"安装包内容",0,20,800,28).Font=new Font(Font,FontStyle.Bold);
            LabelAt(info,"包含 Praat 和 AI 前端程序\nPython 环境需要另行准备；语言模型和语音模型可以安装后添加",0,60,810,58);
            var platform=LabelAt(info,"适用 Windows 10/11 64 位，处理器需支持 x64-v3（含 AVX2）指令集",0,134,810,42);platform.Font=SetupTheme.Small;platform.ForeColor=SetupTheme.Muted;
        }
        void BuildPrerequisitePage()
        {
            dependencies=new DependencyPanel(true,AppDomain.CurrentDomain.BaseDirectory);pages[1].Controls.Add(dependencies);
            dependencies.Fill(request.PythonPath,request.FfmpegPath,request.LlamaServerPath,request.ModelPath,request.MmprojPath,request.AlignmentPaths,request.RuntimeDevice);dependencies.Changed+=(s,e)=>RefreshNext();
        }
        void BuildProgressPage()
        {
            var stack=SetupTheme.Stack();stack.Padding=new Padding(24,20,24,16);pages[2].Controls.Add(stack);
            completionText.Text="准备安装";completionText.Font=new Font(Font.FontFamily,16.5f,FontStyle.Bold);completionText.Height=36;SetupTheme.AddRow(stack,completionText);
            completionDescription.Font=SetupTheme.Small;completionDescription.ForeColor=SetupTheme.Muted;completionDescription.Height=42;SetupTheme.AddRow(stack,completionDescription);
            var current=new Panel{Height=40};progressText.Dock=DockStyle.Fill;progressText.Font=SetupTheme.Small;progressText.TextAlign=ContentAlignment.MiddleLeft;percent.Dock=DockStyle.Right;percent.Width=76;percent.Font=new Font(Font,FontStyle.Bold);percent.TextAlign=ContentAlignment.MiddleRight;current.Controls.Add(progressText);current.Controls.Add(percent);SetupTheme.AddRow(stack,current);
            bar.Name="InstallationProgress";bar.Height=10;bar.Minimum=0;bar.Maximum=100;SetupTheme.AddRow(stack,bar);SetupTheme.AddRow(stack,new Panel{Height=20});
            for(int i=0;i<4;i++){var row=new Panel{Height=42};var title=new Label{Text=new[]{"准备并校验安装文件","复制程序文件","保存路径与 AI 配置","创建启动快捷方式"}[i],Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft,Font=SetupTheme.Small};phaseStates[i]=new Label{Text="待执行",Dock=DockStyle.Right,Width=150,Font=SetupTheme.Small,ForeColor=SetupTheme.Muted,TextAlign=ContentAlignment.MiddleRight,Name="InstallationPhase"+i};row.Controls.Add(title);row.Controls.Add(phaseStates[i]);SetupTheme.Divider(row);SetupTheme.AddRow(stack,row);}
            errorPanel.Name="InstallationFailure";errorPanel.BackColor=Color.FromArgb(255,245,244);errorPanel.BorderStyle=BorderStyle.FixedSingle;errorPanel.Height=110;errorPanel.Visible=false;installationError.Dock=DockStyle.Fill;installationError.Font=SetupTheme.Small;installationError.ForeColor=SetupTheme.Error;installationError.Padding=new Padding(12);errorPanel.Controls.Add(installationError);SetupTheme.AddRow(stack,new Panel{Height=18});SetupTheme.AddRow(stack,errorPanel);
            snapshot.Name="InstallationSummary";snapshot.Font=SetupTheme.Small;snapshot.ForeColor=SetupTheme.Muted;snapshot.Height=152;snapshot.Padding=new Padding(0,16,0,0);SetupTheme.AddRow(stack,snapshot);
            stack.Layout+=(s,e)=>{int width=Math.Max(100,stack.ClientSize.Width-stack.Padding.Horizontal);if(errorPanel.Visible){int height=TextRenderer.MeasureText(installationError.Text,installationError.Font,new Size(width-24,1000),TextFormatFlags.WordBreak).Height+SetupTheme.Px(28);if(errorPanel.Height!=height)errorPanel.Height=height;}int summaryHeight=TextRenderer.MeasureText(snapshot.Text,snapshot.Font,new Size(width,1000),TextFormatFlags.WordBreak).Height+SetupTheme.Px(20);if(snapshot.Height!=summaryHeight)snapshot.Height=summaryHeight;};
        }
        void UpdateProgressPresentation()
        {
            bar.ProgressColor=failure?SetupTheme.Error:finished?SetupTheme.Success:SetupTheme.Primary;bar.Invalidate();percent.Text=bar.Value+"%";
            completionDescription.Text=finished?"请从桌面或开始菜单的 AIPraat 快捷方式启动":failure?"安装遇到问题，可以调整选项或重试":"正在安装所选程序，并保存路径和快捷方式";
            errorPanel.Visible=failure;snapshot.Text=failure?"安装目录："+request.InstallDirectory:"安装目录："+request.InstallDirectory+"\nPython 环境："+request.PythonPath+"\nFFmpeg："+request.FfmpegPath+"\n桌面快捷方式："+(request.CreateDesktopShortcut?"创建 AIPraat 快捷方式":"使用开始菜单的 AIPraat 快捷方式");
            int phase=bar.Value>=95?3:bar.Value>=90?2:bar.Value>=48||(latest!=null&&latest.Message.StartsWith("正在安装："))?1:0;
            for(int i=0;i<4;i++){bool passed=finished||i<phase;phaseStates[i].Text=passed?"已完成":i==phase?(failure?"未完成":"进行中"):"待执行";phaseStates[i].ForeColor=passed?SetupTheme.Success:i==phase?(failure?SetupTheme.Error:SetupTheme.Primary):SetupTheme.Muted;}
        }
        void LoadPrevious()
        {
            if(previousLoaded)return;previousLoaded=true;
            originalAlignmentPaths=request.OriginalAlignmentPaths;
            string python=request.PythonPath,ffmpeg=request.FfmpegPath,llama=request.LlamaServerPath,model=request.ModelPath,mmproj=request.MmprojPath,device=request.RuntimeDevice;
            var alignment=request.AlignmentPaths??new AlignmentPaths();
            string root=PathValidation.Clean(directory.Text),settings=Path.Combine(root,"install-settings.json"),config=Path.Combine(root,"ai","ai_config.json");
            dependencies.SearchDirectory=root;
            try {
                var json=new JavaScriptSerializer();object value;
                if(File.Exists(settings)) {
                    var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(settings));
                    if(saved.TryGetValue("python_path",out value))python=Convert.ToString(value);
                }
                if(File.Exists(config)) {
                    var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(config));var server=Configuration.Object(saved,"server");
                    if(server.TryGetValue("llama_server",out value))llama=Convert.ToString(value);
                    if(server.TryGetValue("model_path",out value))model=Convert.ToString(value);
                    if(server.TryGetValue("mmproj_path",out value))mmproj=Convert.ToString(value);
                    if(Configuration.Object(saved,"audio").TryGetValue("ffmpeg",out value))ffmpeg=Convert.ToString(value);
                    else {
                        string legacy=Path.Combine(root,"ai","anki_config.json");
                        if(File.Exists(legacy)&&json.Deserialize<Dictionary<string,object>>(File.ReadAllText(legacy)).TryGetValue("ffmpeg",out value))ffmpeg=Convert.ToString(value);
                    }
                    alignment=AlignmentPaths.Read(saved);originalAlignmentPaths=alignment;
                    if(Configuration.Object(Configuration.Object(saved,"alignment"),"wav2vec2").TryGetValue("device",out value))device=Convert.ToString(value);
                }
            } catch { /* The transactional installer reports invalid existing configuration. */ }
            dependencies.Fill(python,ffmpeg,llama,model,mmproj,alignment,device);
        }
        void ShowStep(int value)
        {
            step=value;
            dependencies.DiscoveryActions.Visible=step==1;
            subtitle.Text=step==0?"选择安装位置，然后检查运行环境":step==1?"确认必需环境已就绪，按需配置本地工具和模型":finished?"基础设置已完成，请从 AIPraat 快捷方式启动":"查看当前任务和安装进度";
            for(int i=0;i<3;i++) {
                pages[i].Visible=i==step;steps[i].Current=i==step&&!finished;steps[i].Complete=i<step||finished;steps[i].AccessibleDescription=i==step?"当前步骤":i<step||finished?"已完成":"待完成";steps[i].Invalidate();
            }
            if(step==1) {LoadPrevious();}
            previous.Visible=step>0 && !finished; previous.Enabled=!installing&&!dependencies.Busy;
            cancel.Visible=!finished; cancel.Enabled=!installing&&!dependencies.Busy;
            elevate.Visible=step==2 && requireElevation;
            RefreshNext();if(step==2)UpdateProgressPresentation();
        }
        InstallRequest Gather()
        {
            request.InstallDirectory=Path.GetFullPath(PathValidation.Clean(directory.Text)).TrimEnd('\\');
            request.ConfigurePrerequisites=true;request.RuntimeDevice=dependencies.Device;request.CreateDesktopShortcut=desktop.Checked;
            request.PythonPath=dependencies.PythonPath;request.FfmpegPath=dependencies.FfmpegPath;request.LlamaServerPath=dependencies.LlamaPath;request.ModelPath=dependencies.ModelPath;request.MmprojPath=dependencies.MmprojPath;
            request.AlignmentPaths=dependencies.Alignment;request.OriginalAlignmentPaths=originalAlignmentPaths;
            return request;
        }
        void RefreshNext()
        {
            previous.Enabled=cancel.Enabled=!installing&&!dependencies.Busy;
            next.Text=finished?"完成":step==2?(failure?"重试安装":"安装中…"):step==1?"开始安装":"下一步"; next.AccessibleName=next.Text;
            previous.Text=failure&&requireElevation?"返回选择目录":"上一步";previous.Width=SetupTheme.Px(failure&&requireElevation?140:88);
            footerActions.Width=next.Width+next.Margin.Horizontal+(!finished?cancel.Width+cancel.Margin.Horizontal:0)+(step>0&&!finished?previous.Width+previous.Margin.Horizontal:0)+(step==2&&requireElevation?elevate.Width+elevate.Margin.Horizontal:0);
            if(step==0) {
                string error=PathValidation.InstallDirectoryError(directory.Text);
                next.Enabled=error==null;
                directoryNote.ForeColor=error==null?SetupTheme.Muted:SetupTheme.Error;prerequisiteNote.Text=error==null?"安装时创建或复用此目录":"请填写完整的安装目录";prerequisiteNote.ForeColor=error==null?SetupTheme.Muted:SetupTheme.Error;
                directoryNote.Text=SetupTheme.Hint(error ?? (Directory.Exists(PathValidation.Clean(directory.Text))?"该文件夹已存在，将直接复用":"安装时将创建此文件夹"));
                return;
            }
            if(step==1) {
                next.Enabled=dependencies.Ready;prerequisiteNote.Text=dependencies.Ready?"必需环境已就绪，可以开始安装":SetupTheme.Hint(dependencies.ValidationMessage);prerequisiteNote.ForeColor=dependencies.Ready?SetupTheme.Success:SetupTheme.Muted;return;
            }
            next.Enabled=finished || failure;prerequisiteNote.Text=finished?"安装已完成":failure?"请处理安装错误后重试":"正在安装，请等待完成";prerequisiteNote.ForeColor=finished?SetupTheme.Success:failure?SetupTheme.Error:SetupTheme.Muted;
        }
        void Next()
        {
            if(!next.Enabled) return;
            if(finished) {Close();return;}
            if(step==0) {previousLoaded=false; ShowStep(1);return;}
            if(step==1 || failure) StartInstallation();
        }
        public void Resume()
        {
            directory.Text=request.InstallDirectory;desktop.Checked=request.CreateDesktopShortcut;
            dependencies.Fill(request.PythonPath,request.FfmpegPath,request.LlamaServerPath,request.ModelPath,request.MmprojPath,request.AlignmentPaths,request.RuntimeDevice);
            originalAlignmentPaths=request.OriginalAlignmentPaths;previousLoaded=true;
            ShowStep(2);Shown+=(s,e)=>RunInstallation(request);
        }
        void StartInstallation() { RunInstallation(Gather()); }
        async void RunInstallation(InstallRequest plan)
        {
            failure=finished=requireElevation=false; installing=true; latest=null; bar.Value=0;
            completionText.Text="正在安装 AIPraat";
            progressText.Text="正在再次验证运行环境…";installationError.Text="";
            ShowStep(2); progressTimer.Start();
            try {
                await Task.Run(()=>{
                    using(var payload=payloadFactory()) new InstallEngine().Run(plan,payload,x=>latest=x);
                });
                ApplyProgress();bar.Value=100;finished=true;
                completionText.Text="安装已完成";
                progressText.Text="程序、配置和启动快捷方式已就绪";
            } catch(Exception e) {
                failure=true;requireElevation=e is UnauthorizedAccessException;
                completionText.Text="安装未完成";
                installationError.Text=e.Message+(requireElevation?"\n此位置需要写入权限；可返回选择其他目录，或以管理员权限重试":"\n请处理上述问题后重试，或返回修改安装选项");ApplyProgress();
            } finally {
                installing=false;progressTimer.Stop();ShowStep(2);
            }
        }
        void ApplyProgress()
        {
            var p=latest;if(p==null)return;bar.Value=p.Percent;progressText.Text=p.Message;UpdateProgressPresentation();
        }
        void Elevate()
        {
            string path=Path.Combine(Path.GetTempPath(),"AIPraat-resume-"+Guid.NewGuid().ToString("N")+".json");
            try {
                File.WriteAllText(path,new JavaScriptSerializer().Serialize(request),new System.Text.UTF8Encoding(false));
                Process.Start(new ProcessStartInfo(Assembly.GetExecutingAssembly().Location,
                    "--resume "+PathValidation.QuoteArgument(path)){UseShellExecute=true,Verb="runas"});
                Close();
            } catch(System.ComponentModel.Win32Exception) {
                if(File.Exists(path))File.Delete(path);
                progressText.Text="管理员权限未获批准；可返回选择可写入的文件夹";
            } catch(Exception e) { progressText.Text=e.Message; }
        }
    }
}
