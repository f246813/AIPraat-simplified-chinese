using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AIPraat.Setup
{
    public sealed class RuntimeDependencyField : Panel
    {
        readonly Label summary=new Label();
        readonly OutlineButton configure=new OutlineButton();
        public event EventHandler Configure;
        public RuntimeDependencyField()
        {
            Size=new Size(820,48);Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;Name="RuntimeDependencies";
            summary.Location=new Point(0,8);summary.Size=new Size(580,30);summary.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;
            configure.Text="配置运行库…";configure.Name="ConfigureRuntime";configure.Location=new Point(692,3);configure.Size=new Size(128,34);configure.Anchor=AnchorStyles.Top|AnchorStyles.Right;
            Controls.Add(summary);Controls.Add(configure);configure.Click+=(s,e)=>{if(Configure!=null)Configure(this,e);};
            Refresh(null);
        }
        public void Refresh(PythonProbe probe)
        {
            var report=probe==null?null:probe.Report;
            int ready=report==null||report.items==null?0:report.items.Count(x=>x.ok);
            summary.Text="运行库（必需）："+(report==null||!report.completed?"待检测":"已就绪 "+ready+"/"+RuntimeDependencies.Items().Length+" 项");
            summary.ForeColor=probe!=null&&probe.Valid?Color.FromArgb(30,110,50):Color.Firebrick;
        }
    }
    public sealed class RuntimeDependenciesForm : Form
    {
        readonly string python;
        readonly DataGridView grid=new DataGridView();
        readonly ComboBox device=new ComboBox();
        readonly TextBox dependency=new TextBox(), log=new TextBox();
        readonly Label status=new Label();
        readonly ProgressBar progress=new ProgressBar();
        readonly OutlineButton local=new OutlineButton(), online=new OutlineButton(), detect=new OutlineButton(), close=new OutlineButton();
        readonly Panel options=new Panel();
        readonly LinkLabel savedLog=new LinkLabel();
        string logPath;
        readonly Dictionary<string,string> selected=new Dictionary<string,string>();
        string[] identifiedFolders=new string[0];
        bool busy;
        public PythonProbe Probe {get;private set;}
        public string Device {get{return device.SelectedIndex==1?"cuda":"cpu";}}
        public RuntimeDependenciesForm(string python,string selectedDevice) : this(python,selectedDevice,null) {}
        public RuntimeDependenciesForm(string python,string selectedDevice,RuntimeInstallPlan plan)
        {
            this.python=PathValidation.Clean(python);Text="运行库配置（必需）";Name="RuntimeDependenciesForm";
            Font=new Font("Microsoft YaHei UI",9.5f);BackColor=Color.White;StartPosition=FormStartPosition.CenterParent;
            ClientSize=new Size(1160,830);MinimumSize=new Size(980,720);AutoScaleMode=AutoScaleMode.Dpi;
            var layout=new TableLayoutPanel {Dock=DockStyle.Fill,ColumnCount=1,RowCount=6,Padding=new Padding(18)};
            foreach(float size in new[]{76f,400f,94f})layout.RowStyles.Add(new RowStyle(SizeType.Absolute,size));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,54));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,52));Controls.Add(layout);
            var header=new Label {Dock=DockStyle.Fill,Text="当前 Python："+this.python+"\n需要 Python 3.10–3.12 x64，推荐 3.12。已就绪的运行库无需提供安装包；检测不下载模型。"};layout.Controls.Add(header,0,0);
            grid.Dock=DockStyle.Fill;grid.AllowUserToAddRows=grid.AllowUserToDeleteRows=false;grid.RowHeadersVisible=false;
            grid.ReadOnly=true;grid.AutoSizeColumnsMode=DataGridViewAutoSizeColumnsMode.Fill;grid.AutoSizeRowsMode=DataGridViewAutoSizeRowsMode.AllCells;
            grid.BackgroundColor=Color.White;grid.DefaultCellStyle.WrapMode=DataGridViewTriState.True;grid.SelectionMode=DataGridViewSelectionMode.FullRowSelect;
            foreach(string column in new[]{"运行库","必需","要求版本","已安装版本","检测状态","本地安装包路径"})grid.Columns.Add(column,column);
            grid.Columns[0].FillWeight=100;grid.Columns[1].FillWeight=35;grid.Columns[2].FillWeight=165;grid.Columns[3].FillWeight=85;grid.Columns[4].FillWeight=90;grid.Columns[5].FillWeight=140;
            grid.Columns.Add(new DataGridViewButtonColumn {Name="BrowseRuntime",HeaderText="选择安装包",Text="浏览…",UseColumnTextForButtonValue=false,FillWeight=60});
            foreach(var item in RuntimeDependencies.Items())grid.Rows.Add(item.label,"必需",item.requirement,"—","待检测","",item.id=="tk"?"修复说明":"浏览…");
            if(plan!=null) {
                identifiedFolders=plan.dependency_folders??new string[0];
                foreach(var pair in plan.packages)selected[pair.Key]=pair.Value;
                if(!string.IsNullOrEmpty(plan.webview2))selected["webview2"]=plan.webview2;
                foreach(DataGridViewRow row in grid.Rows) {string id=RuntimeDependencies.Items()[row.Index].id;string path;if(selected.TryGetValue(id,out path))row.Cells[5].Value=path;}
                dependency.Text=plan.dependency_folder;
            }
            for(int i=0;i<grid.Rows.Count;i++)grid.Rows[i].Cells[2].ToolTipText=Convert.ToString(grid.Rows[i].Cells[2].Value);
            layout.Controls.Add(grid,0,1);grid.CellContentClick+=(s,e)=>{if(e.RowIndex>=0&&e.ColumnIndex==6&&!busy)Browse(e.RowIndex);};
            options.Dock=DockStyle.Fill;layout.Controls.Add(options,0,2);
            options.Controls.Add(new Label {Text="间接依赖包文件夹：",Location=new Point(0,14),Size=new Size(154,26)});
            dependency.Name="DependencyFolder";dependency.Location=new Point(158,10);dependency.Size=new Size(700,30);dependency.Anchor=AnchorStyles.Left|AnchorStyles.Right|AnchorStyles.Top;options.Controls.Add(dependency);
            var folder=new OutlineButton {Text="浏览…",Location=new Point(878,8),Size=new Size(116,34),Anchor=AnchorStyles.Top|AnchorStyles.Right};options.Controls.Add(folder);
            folder.Click+=(s,e)=>{using(var dialog=new FolderBrowserDialog{Description="选择存放间接依赖 wheel 的文件夹"})if(dialog.ShowDialog(this)==DialogResult.OK)dependency.Text=dialog.SelectedPath;};
            options.Controls.Add(new Label {Text="PyTorch / wav2vec2 设备：",Location=new Point(0,58),Size=new Size(215,26)});
            device.Location=new Point(222,54);device.Width=310;device.DropDownStyle=ComboBoxStyle.DropDownList;
            device.Items.AddRange(new object[]{"CPU（无需 CUDA）","CUDA（必须验证设备可用）"});device.SelectedIndex=selectedDevice=="cuda"?1:0;device.Name="RuntimeDevice";options.Controls.Add(device);
            device.SelectedIndexChanged+=(s,e)=>{Probe=null;UpdateRows();status.Text="设备已切换，请重新检测。";};
            log.Multiline=true;log.ReadOnly=true;log.ScrollBars=ScrollBars.Both;log.WordWrap=false;log.Dock=DockStyle.Fill;log.Name="RuntimeLog";layout.Controls.Add(log,0,3);
            var state=new Panel{Dock=DockStyle.Fill};layout.Controls.Add(state,0,4);status.Location=new Point(0,5);status.Size=new Size(1080,27);status.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;state.Controls.Add(status);
            progress.Location=new Point(0,34);progress.Size=new Size(1100,8);progress.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;state.Controls.Add(progress);
            var footer=new FlowLayoutPanel {Dock=DockStyle.Fill,FlowDirection=FlowDirection.LeftToRight};layout.Controls.Add(footer,0,5);
            local.Text="安装所选本地包";online.Text="运行 PowerShell 联网补齐";detect.Text="重新检测";close.Text="关闭";
            local.Name="InstallLocalRuntime";online.Name="InstallOnlineRuntime";detect.Name="DetectRuntime";close.Name="CloseRuntime";
            foreach(var button in new[]{local,online,detect,close}) {button.Width=button==online?250:button==local?185:120;button.Height=38;button.Margin=new Padding(0,7,14,0);footer.Controls.Add(button);}
            savedLog.Text="查看本次日志";savedLog.Name="OpenRuntimeLog";savedLog.Size=new Size(180,38);savedLog.Margin=new Padding(0,7,0,0);savedLog.TextAlign=ContentAlignment.MiddleLeft;savedLog.Enabled=false;footer.Controls.Add(savedLog);
            savedLog.LinkClicked+=(s,e)=>{if(!string.IsNullOrEmpty(logPath)&&File.Exists(logPath))System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("notepad.exe",PathValidation.QuoteArgument(logPath)){UseShellExecute=true});};
            local.Click+=(s,e)=>Run(false);online.Click+=(s,e)=>Run(true);detect.Click+=(s,e)=>Check();close.Click+=(s,e)=>Close();
            FormClosing+=(s,e)=>{if(busy)e.Cancel=true;};Shown+=(s,e)=>Check();
        }
        void Browse(int index)
        {
            var item=RuntimeDependencies.Items()[index];
            if(item.id=="tk") {MessageBox.Show(this,"Tcl/Tk 随 Python/Conda 提供。联网补齐沿用所选环境的官方 Python/Conda 修复流程；离线时请用该环境的 Python 安装器或 Conda 缓存补装 Tk，再重新检测。","Tcl/Tk 修复");return;}
            using(var dialog=new OpenFileDialog {Title="选择 "+item.label+" 的本地安装包",Filter=item.id=="webview2"?"微软 WebView2 x64 离线完整安装程序|*.exe":"Python wheel|*.whl",CheckFileExists=true}) {
                if(dialog.ShowDialog(this)!=DialogResult.OK)return;
                selected[item.id]=dialog.FileName;grid.Rows[index].Cells[5].Value=dialog.FileName;grid.Rows[index].Cells[5].ToolTipText=dialog.FileName;
            }
        }
        void SetBusy(bool value)
        {
            busy=value;options.Enabled=grid.Enabled=local.Enabled=online.Enabled=detect.Enabled=close.Enabled=!value;
            progress.Style=value?ProgressBarStyle.Marquee:ProgressBarStyle.Continuous;
        }
        void Append(string line)
        {
            if(IsDisposed)return;
            if(InvokeRequired){BeginInvoke(new Action<string>(Append),line);return;}
            log.AppendText(line+Environment.NewLine);status.Text=line.Length>140?line.Substring(0,140):line;
        }
        async void Check()
        {
            if(busy)return;SetBusy(true);Probe=null;SetLog(null);UpdateRows();
            string selectedDevice=Device;status.Text="正在逐项检测运行库并启动一次新前端窗口…";
            try {Probe=await Task.Run(()=>RuntimeDependencies.Probe(python,selectedDevice,true,Append));SetLog(Probe.Report==null?null:Probe.Report.log_path);UpdateRows();status.Text=Probe.Message;}
            finally {SetBusy(false);}
        }
        void SetLog(string path)
        {
            logPath=path;savedLog.Enabled=!string.IsNullOrEmpty(path)&&File.Exists(path);
        }
        void UpdateRows()
        {
            var report=Probe==null?null:Probe.Report;var items=RuntimeDependencies.Items();
            for(int i=0;i<items.Length;i++) {
                var item=report==null||report.items==null?null:report.items.FirstOrDefault(x=>x.id==items[i].id);
                grid.Rows[i].Cells[3].Value=item==null?"—":item.installed;
                string text=item==null?"待检测":item.status;
                grid.Rows[i].Cells[4].Value=text.Length>150?text.Substring(text.Length-150):text;
                grid.Rows[i].Cells[4].ToolTipText=text;
                grid.Rows[i].Cells[4].Style.ForeColor=item!=null&&item.ok?Color.DarkGreen:Color.Firebrick;
            }
        }
        async void Run(bool useOnline)
        {
            if(busy)return;
            var plan=new RuntimeInstallPlan {device=Device,dependency_folder=PathValidation.Clean(dependency.Text),dependency_folders=identifiedFolders,before=Probe==null?null:Probe.Report};
            foreach(var pair in selected) {if(pair.Key=="webview2")plan.webview2=pair.Value;else plan.packages.Add(pair.Key,pair.Value);}
            SetBusy(true);Probe=null;SetLog(null);UpdateRows();
            try {
                var result=await Task.Run(()=>RuntimeDependencies.Install(python,plan,useOnline,Append));
                Probe=result.Probe;SetLog(result.LogPath);
                UpdateRows();status.Text=Probe.Message;
            } catch(Exception error) {Append("配置失败："+error.Message);}
            finally {SetBusy(false);}
        }
    }
}
