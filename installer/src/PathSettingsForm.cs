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
        readonly TextBox python=new TextBox(), llama=new TextBox(), mmproj=new TextBox();
        readonly OutlineButton save=new OutlineButton(), cancel=new OutlineButton();
        readonly Label note=new Label();
        readonly PythonSetupGuide setup=new PythonSetupGuide();
        readonly ToolTip tips=new ToolTip();
        readonly Timer pythonTimer=new Timer {Interval=650}, parentTimer=new Timer {Interval=500};
        readonly Panel fields=new Panel();
        readonly AlignmentPathFields alignmentFields=new AlignmentPathFields();
        PythonProbe probe;
        int generation;
        bool saving,configuringPython;
        [DllImport("kernel32.dll",SetLastError=true)]
        static extern SafeProcessHandle OpenProcess(uint access,bool inheritHandle,int processId);
        [DllImport("kernel32.dll",SetLastError=true)]
        static extern bool GetExitCodeProcess(SafeProcessHandle process,out uint exitCode);
        static bool ParentHasExited(int processId)
        {
            // Process.GetProcessById can report a live higher-integrity parent as absent.
            // Query only its exit status; failure to query is not evidence of an exit.
            using(var process=OpenProcess(0x1000,false,processId)) { // PROCESS_QUERY_LIMITED_INFORMATION
                if(process.IsInvalid)return Marshal.GetLastWin32Error()==87; // ERROR_INVALID_PARAMETER: PID no longer exists
                uint exitCode;
                return GetExitCodeProcess(process,out exitCode) && exitCode!=259; // STILL_ACTIVE
            }
        }
        public PathSettingsForm(PathSettings settings,string resultPath,int parentPid)
        {
            this.settings=settings; this.resultPath=resultPath; this.parentPid=parentPid;
            Text="AIPraat 路径配置"; Name="AIPraatPathSettings"; AccessibleName=Text;
            Font=new Font("Microsoft YaHei UI",9.5f); BackColor=Color.White;
            ClientSize=new Size(900,690); MinimumSize=new Size(820,650); AutoScaleMode=AutoScaleMode.Dpi;
            StartPosition=FormStartPosition.CenterScreen; MaximizeBox=false;
            var layout=new TableLayoutPanel {Dock=DockStyle.Fill,RowCount=4,ColumnCount=1,Padding=new Padding(28,20,28,14)};
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute,60));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,90));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,62));Controls.Add(layout);
            var title=new Label {Text="路径配置",Dock=DockStyle.Fill,Font=new Font(Font.FontFamily,19,FontStyle.Bold)};layout.Controls.Add(title,0,0);
            fields.Dock=DockStyle.Fill;fields.AutoScroll=true;layout.Controls.Add(fields,0,1);
            FilePathField.Add(fields,python,"Python 环境（必需）","选择环境中的 python.exe",0,"PythonPath","Python 解释器|python.exe|可执行文件|*.exe",tips);
            FilePathField.Add(fields,llama,"llama.cpp（可选）","选择 llama-server.exe，可留空",79,"LlamaServerPath","llama-server|llama-server.exe|可执行文件|*.exe",tips);
            FilePathField.Add(fields,mmproj,"视觉投影（可选）","选择与模型匹配的 mmproj .gguf，可留空",158,"MmprojPath","GGUF 视觉投影|*.gguf",tips);
            alignmentFields.Add(fields,237,tips,false,false);
            var validation=new Panel {Dock=DockStyle.Fill};layout.Controls.Add(validation,0,2);
            note.Location=new Point(0,4);note.Size=new Size(820,46);note.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;validation.Controls.Add(note);
            setup.Location=new Point(0,52);validation.Controls.Add(setup);setup.LinkClicked+=(s,e)=>ConfigurePython();
            var footer=new FlowLayoutPanel {Dock=DockStyle.Fill,FlowDirection=FlowDirection.RightToLeft,Padding=new Padding(0,12,0,0)};layout.Controls.Add(footer,0,3);
            save.Text="保存";save.Name="SavePaths";save.AccessibleName="保存";save.Enabled=false;
            cancel.Text="取消";cancel.Name="CancelPaths";cancel.DialogResult=DialogResult.Cancel;
            foreach(var button in new[]{save,cancel}) {button.Size=new Size(108,38);button.Margin=new Padding(8,0,0,0);footer.Controls.Add(button);}
            AcceptButton=save;CancelButton=cancel;
            python.Text=settings.PythonPath;llama.Text=settings.LlamaServerPath;mmproj.Text=settings.MmprojPath;
            alignmentFields.Fill(settings.AlignmentPaths);alignmentFields.Changed+=(s,e)=>RefreshValidation();
            python.TextChanged+=(s,e)=>{generation++;probe=null;pythonTimer.Stop();pythonTimer.Start();RefreshValidation();};
            llama.TextChanged+=(s,e)=>RefreshValidation();mmproj.TextChanged+=(s,e)=>RefreshValidation();
            pythonTimer.Tick+=(s,e)=>{pythonTimer.Stop();CheckPython();};
            parentTimer.Tick+=(s,e)=>{
                if(parentPid<=0 || saving || configuringPython) return;
                if(!ParentHasExited(parentPid))return;
                Close();
            };
            save.Click+=(s,e)=>Save();cancel.Click+=(s,e)=>Close();
            FormClosing+=(s,e)=>{if(saving||configuringPython)e.Cancel=true;};
            FormClosed+=(s,e)=>{generation++;pythonTimer.Dispose();parentTimer.Dispose();tips.Dispose();};
            Shown+=(s,e)=>{pythonTimer.Start();if(parentPid>0)parentTimer.Start();};
            RefreshValidation();
        }
        async void CheckPython()
        {
            if(configuringPython||IsDisposed)return;
            string target=PathValidation.Clean(python.Text);int version=++generation;probe=null;RefreshValidation();
            var checkedProbe=await Task.Run(()=>PathValidation.ProbePython(target));
            if(IsDisposed || version!=generation || target!=PathValidation.Clean(python.Text)) return;
            probe=checkedProbe;RefreshValidation();
        }
        void RefreshValidation()
        {
            setup.Refresh(probe,true,configuringPython);
            if(saving||configuringPython)return;
            string error=PathValidation.OptionalPathsError(new InstallRequest {LlamaServerPath=llama.Text,MmprojPath=mmproj.Text,AlignmentPaths=alignmentFields.Read(),OriginalAlignmentPaths=settings.AlignmentPaths});
            bool valid=probe!=null && probe.Valid && error==null;
            save.Enabled=valid;note.ForeColor=valid?Color.FromArgb(30,110,50):Color.Firebrick;
            note.Text=error ?? (probe!=null?probe.Message:python.Text==""?"请选择 Python 3.10+ 的 python.exe；需含 Tk、NumPy、Pillow 与云端编排依赖，可用下方一键配置补齐。":"正在检查 Python 环境…");
        }
        async void ConfigurePython()
        {
            if(configuringPython||saving||probe==null||!probe.CanConfigure)return;
            string selected=PathValidation.Clean(python.Text);
            configuringPython=true;generation++;pythonTimer.Stop();save.Enabled=cancel.Enabled=fields.Enabled=false;
            setup.Refresh(probe,true,true);note.Text="正在配置所选 Python 环境，请在 PowerShell 窗口查看进度。";
            try {await Task.Run(()=>PythonSetup.Run(selected));}
            catch(Exception e) {MessageBox.Show(this,"无法启动一键配置："+e.Message,"运行环境配置",MessageBoxButtons.OK,MessageBoxIcon.Error);}
            finally {
                configuringPython=false;
                if(!IsDisposed){cancel.Enabled=fields.Enabled=true;CheckPython();}
            }
        }
        async void Save()
        {
            if(!save.Enabled || saving)return;
            string selectedPython=python.Text, selectedLlama=llama.Text, selectedMmproj=mmproj.Text;
            var selectedAlignment=alignmentFields.Read();
            saving=true;save.Enabled=cancel.Enabled=fields.Enabled=false;note.Text="正在保存路径…";
            try {
                await Task.Run(()=>settings.SavePaths(selectedPython,selectedLlama,selectedMmproj,resultPath,selectedAlignment));
                saving=false;DialogResult=DialogResult.OK;Close();
            } catch(Exception e) {
                saving=false;cancel.Enabled=fields.Enabled=true;RefreshValidation();
                note.ForeColor=Color.Firebrick;note.Text="保存失败："+e.Message;
            }
        }
    }
}
