using System;
using System.IO;
using System.Reflection;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;
using AIPraat.Setup;

static class PythonSetupTests
{
    static int failures;
    static void Assert(bool value,string message) {if(!value)throw new Exception(message);}
    static void Case(string name,Action test) {try{test();Console.WriteLine("PASS "+name);}catch(Exception e){failures++;Console.WriteLine("FAIL "+name+": "+(e.InnerException??e).Message);}}
    static bool CanConfigure(PythonProbe probe) {
        var property=typeof(PythonProbe).GetProperty("CanConfigure");
        Assert(property!=null,"尚未区分可配置的依赖缺失");return (bool)property.GetValue(probe,null);
    }
    static void SetProbe(Form form,PythonProbe probe,string refresh) {
        var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];
        var flags=BindingFlags.Instance|BindingFlags.NonPublic;
        typeof(DependencyPanel).GetField("pythonProbe",flags).SetValue(panel,probe);
        var verified=(System.Collections.Generic.Dictionary<string,DependencyCandidate>)typeof(DependencyPanel).GetField("verified",flags).GetValue(panel);
        verified["python"]=new DependencyCandidate{Kind="python",Path=panel.PythonPath};
        typeof(DependencyPanel).GetMethod("RefreshState",flags).Invoke(panel,null);Application.DoEvents();
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        var missing=PathValidation.ProbePython(args[1]);var valid=PathValidation.ProbePython(args[0]);
        string root=Path.Combine(Path.GetTempPath(),"AIPraat setup 中文 "+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        Case("repair_available_only_for_runnable_supported_python_missing_dependencies",()=>{
            Assert(!missing.Valid&&CanConfigure(missing),"缺依赖环境无一键配置");
            Assert(!CanConfigure(valid)&&!CanConfigure(PathValidation.ProbePython("missing.exe")),"可用或无解释器环境错误提供依赖安装");
        });
        Case("single_runtime_entry_has_disabled_ready_and_busy_states",()=>{
            using(var form=new PathSettingsForm(new PathSettings(Path.Combine(root,"entry-states"),Path.Combine(root,"entry-states","ai_config.json"),Path.Combine(root,"entry-profile"),""),"",0)) {
                var button=form.Controls.Find("RuntimeDetails",true)[0];var link=button;Assert(form.Controls.Find("RuntimeDetails",true).Length==1&&form.Controls.Find("ConfigureRuntime",true).Length==0,"运行库入口重复");
                Assert(!button.Enabled&&!link.Enabled,"未验证 Python 时两个入口应禁用");
                SetProbe(form,valid,"RefreshValidation");Assert(button.Enabled&&link.Enabled,"Python 就绪后入口未同时启用");
                var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];
                typeof(DependencyPanel).GetMethod("SetBusy",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(panel,new object[]{true});
                Assert(!button.Enabled&&!link.Enabled,"忙碌时两个入口应禁用");
                typeof(DependencyPanel).GetMethod("SetBusy",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(panel,new object[]{false});
                Assert(button.Enabled&&link.Enabled,"任务结束后入口状态未恢复");
            }
        });
        Case("path_dialog_runtime_field_available_for_missing_and_ready_environment",()=>{
            using(var form=new PathSettingsForm(new PathSettings(Path.Combine(root,"ai"),Path.Combine(root,"ai","ai_config.json"),Path.Combine(root,"profile"),args[1]),"",0)) {
                form.Show();SetProbe(form,missing,"RefreshValidation");
                var controls=form.Controls.Find("RuntimeDetails",true);Assert(controls.Length==1,"路径配置缺少运行库配置入口");
                Assert(controls[0].Visible&&controls[0].Enabled&&controls[0].Text=="运行库明细…","配置入口状态不正确");
                Assert(controls[0].Parent.ClientRectangle.Contains(controls[0].Bounds),"引导被较长的路径列表遮挡");
                using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"paths-missing.png"));}
                SetProbe(form,valid,"RefreshValidation");Assert(controls[0].Visible&&controls[0].Enabled,"环境就绪后运行库入口消失");form.Close();
            }
        });
        Case("installer_uses_same_runtime_field_and_removes_skip",()=>{
            using(var form=new WizardForm(new InstallRequest{InstallDirectory=Path.Combine(root,"install")},()=>null)) {
                form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();
                ((TextBox)form.Controls.Find("PythonPath",true)[0]).Text=args[1];
                SetProbe(form,missing,"RefreshNext");var controls=form.Controls.Find("RuntimeDetails",true);
                Assert(controls.Length==1&&controls[0].Visible,"安装器缺少同款引导");
                Assert(controls[0].Text=="运行库明细…","安装器文案不一致");
                Assert(controls[0].Parent.ClientRectangle.Contains(controls[0].Bounds),"安装器引导被较长的路径列表遮挡："+controls[0].Bounds+" / "+controls[0].Parent.ClientRectangle);
                using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"installer-missing.png"));}
                Assert(form.Controls.Find("SkipPrerequisites",true).Length==0,"仍存在跳过入口");form.Close();
            }
        });
        Case("restored_button_opens_original_detection_installation_and_repair_window",()=>{
            using(var form=new PathSettingsForm(new PathSettings(Path.Combine(root,"modal-ai"),Path.Combine(root,"modal-ai","ai_config.json"),Path.Combine(root,"modal-profile"),args[0]),"",0))
            using(var timer=new Timer{Interval=50}) {
                form.Show();SetProbe(form,valid,"RefreshValidation");bool opened=false;string error=null;
                timer.Tick+=(s,e)=>{
                    RuntimeDependenciesForm dialog=null;
                    foreach(Form current in Application.OpenForms)if(current is RuntimeDependenciesForm)dialog=(RuntimeDependenciesForm)current;
                    if(dialog==null||(bool)typeof(RuntimeDependenciesForm).GetField("busy",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(dialog))return;
                    try {
                        var grid=(DataGridView)typeof(RuntimeDependenciesForm).GetField("grid",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(dialog);
                        Assert(grid.Rows.Count==13,"缺少原来的逐项检测清单");
                        foreach(string name in new[]{"DetectRuntime","InstallLocalRuntime","InstallOnlineRuntime","DependencyFolder","RuntimeDevice"})Assert(dialog.Controls.Find(name,true).Length==1,"缺少检测/修复功能 "+name);
                        Assert(dialog.Probe!=null&&dialog.Probe.Valid,"真实运行库检测未通过");
                        using(var bitmap=new Bitmap(dialog.Width,dialog.Height)){dialog.DrawToBitmap(bitmap,new Rectangle(Point.Empty,dialog.Size));bitmap.Save(Path.Combine(root,"runtime-restored.png"));}
                        opened=true;
                    }catch(Exception failure){error=failure.Message;}
                    timer.Stop();dialog.Close();
                };
                timer.Start();((Button)form.Controls.Find("RuntimeDetails",true)[0]).PerformClick();timer.Stop();
                Assert(opened,error??"按钮未打开原来的运行库窗口");form.Close();
            }
        });
        Case("powershell_launch_preserves_unicode_spaces_apostrophes_and_metacharacters",()=>{
            var type=typeof(PathValidation).Assembly.GetType("AIPraat.Setup.PythonSetup");Assert(type!=null,"PowerShell 安装尚未实现");
            string script=Path.Combine(root,"测试 O'Brien & $.ps1"), result=Path.Combine(root,"argument.txt"), value=Path.Combine(root,"Python O'Brien & $环境","python.exe");
            File.WriteAllText(script,"param([string]$PythonPath)\n[IO.File]::WriteAllText('"+result.Replace("'","''")+"',$PythonPath)\n",new System.Text.UTF8Encoding(true));
            var start=(ProcessStartInfo)type.GetMethod("CreateStartInfo").Invoke(null,new object[]{script,value});start.UseShellExecute=false;start.CreateNoWindow=true;
            using(var process=Process.Start(start)){Assert(process.WaitForExit(15000)&&process.ExitCode==0,"参数测试未成功执行");}
            Assert(File.ReadAllText(result)==value,"路径被 PowerShell 解析为命令");
        });
        Console.WriteLine("Screenshots: "+root);return failures==0?0:1;
    }
}
