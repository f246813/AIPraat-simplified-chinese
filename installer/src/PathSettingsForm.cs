using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;

namespace AIPraat.Setup
{
    public sealed class PathSettingsForm : Form
    {
        readonly PathSettings settings;
        readonly string resultPath;
        readonly int parentPid;
        readonly OutlineButton save=new OutlineButton(),cancel=new OutlineButton();
        readonly Label note=new Label();
        readonly Timer parentTimer=new Timer{Interval=500};
        readonly DependencyPanel dependencies;
        bool saving;
        [DllImport("kernel32.dll",SetLastError=true)]static extern SafeProcessHandle OpenProcess(uint access,bool inheritHandle,int processId);
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetExitCodeProcess(SafeProcessHandle process,out uint exitCode);
        static bool ParentHasExited(int processId)
        {
            using(var process=OpenProcess(0x1000,false,processId)) {
                if(process.IsInvalid)return Marshal.GetLastWin32Error()==87;
                uint exitCode;return GetExitCodeProcess(process,out exitCode)&&exitCode!=259;
            }
        }
        public PathSettingsForm(PathSettings settings,string resultPath,int parentPid)
        {
            this.settings=settings;this.resultPath=resultPath;this.parentPid=parentPid;
            Text="AIPraat 路径配置";Name="AIPraatPathSettings";AccessibleName=Text;
            Font=SetupTheme.Body;ForeColor=SetupTheme.Text;BackColor=Color.White;ClientSize=new Size(900,720);MinimumSize=new Size(820,650);AutoScaleMode=AutoScaleMode.Dpi;AutoScaleDimensions=new SizeF(96,96);StartPosition=FormStartPosition.CenterScreen;MaximizeBox=false;
            var layout=new TableLayoutPanel{Dock=DockStyle.Fill,RowCount=3,ColumnCount=1,Margin=Padding.Empty};
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute,88));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,76));Controls.Add(layout);
            var header=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty};layout.Controls.Add(header,0,0);SetupTheme.Divider(header);
            header.Controls.Add(new Label{Text="路径配置",Location=new Point(24,16),Size=new Size(340,32),Font=new Font(Font.FontFamily,16.5f,FontStyle.Bold)});
            header.Controls.Add(new Label{Text="设置程序位置，查看环境检测结果。",Location=new Point(24,52),Size=new Size(420,24),Font=SetupTheme.Small,ForeColor=SetupTheme.Muted});
            dependencies=new DependencyPanel(false,AppDomain.CurrentDomain.BaseDirectory);layout.Controls.Add(dependencies,0,1);
            dependencies.Margin=Padding.Empty;var actions=dependencies.DiscoveryActions;actions.Location=new Point(612,24);actions.Anchor=AnchorStyles.Top|AnchorStyles.Right;header.Controls.Add(actions);
            dependencies.Fill(settings.PythonPath,settings.FfmpegPath,settings.LlamaServerPath,settings.ModelPath,settings.MmprojPath,settings.AlignmentPaths,settings.RuntimeDevice);
            dependencies.Changed+=(s,e)=>RefreshValidation();
            var footerPanel=new Panel{Dock=DockStyle.Fill,Margin=Padding.Empty,Padding=new Padding(24,1,24,0)};layout.Controls.Add(footerPanel,0,2);
            footerPanel.Paint+=(s,e)=>{using(var pen=new Pen(SetupTheme.Line))e.Graphics.DrawLine(pen,0,0,footerPanel.Width,0);};
            note.Dock=DockStyle.Fill;note.TextAlign=ContentAlignment.MiddleLeft;note.Font=SetupTheme.Small;note.Name="PathValidationMessage";footerPanel.Controls.Add(note);
            var footer=new FlowLayoutPanel{Dock=DockStyle.Right,Width=196,FlowDirection=FlowDirection.RightToLeft,WrapContents=false,Padding=new Padding(0,18,0,0)};footerPanel.Controls.Add(footer);
            save.Text="保存";save.Name="SavePaths";save.AccessibleName="保存";save.Enabled=false;cancel.Text="取消";cancel.Name="CancelPaths";cancel.DialogResult=DialogResult.Cancel;
            save.Primary=true;foreach(var button in new[]{save,cancel}){button.Size=new Size(88,40);button.Margin=new Padding(8,0,0,0);footer.Controls.Add(button);}
            AcceptButton=save;CancelButton=cancel;save.Click+=(s,e)=>Save();cancel.Click+=(s,e)=>Close();
            parentTimer.Tick+=(s,e)=>{if(parentPid>0&&!saving&&!dependencies.Busy&&ParentHasExited(parentPid))Close();};
            FormClosing+=(s,e)=>{if(saving||dependencies.Busy)e.Cancel=true;};FormClosed+=(s,e)=>parentTimer.Dispose();
            Shown+=(s,e)=>{SetupTheme.FitToScreen(this);if(parentPid>0)parentTimer.Start();};RefreshValidation();
        }
        void RefreshValidation()
        {
            if(saving)return;save.Enabled=dependencies.Ready;cancel.Enabled=!dependencies.Busy;note.Text=dependencies.ValidationMessage;note.ForeColor=dependencies.Ready?SetupTheme.Success:SetupTheme.Muted;
        }
        async void Save()
        {
            if(!save.Enabled||saving)return;
            string python=dependencies.PythonPath,llama=dependencies.LlamaPath,mmproj=dependencies.MmprojPath,ffmpeg=dependencies.FfmpegPath,device=dependencies.Device;var alignment=dependencies.Alignment;
            saving=true;save.Enabled=cancel.Enabled=dependencies.Enabled=false;note.Text="正在再次验证依赖并保存路径…";
            try {await Task.Run(()=>settings.SavePaths(python,llama,mmproj,resultPath,alignment,device,ffmpeg));saving=false;DialogResult=DialogResult.OK;Close();}
            catch(Exception error){saving=false;dependencies.Enabled=true;RefreshValidation();note.ForeColor=Color.Firebrick;note.Text="保存失败："+error.Message;dependencies.StartChecks();}
        }
    }
}
