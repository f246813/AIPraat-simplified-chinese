using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Collections.Generic;
using System.Windows.Forms;
using AIPraat.Setup;

static class RuntimeLogTests
{
    static int failed,passed,skipped;
    static bool scratchOnly;
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Case(string name,Action action){
        if(scratchOnly&&(name.StartsWith("all_logs_denied_")||name=="denied_user_and_temp_logs_use_program_directory"||name=="denied_report_archive_does_not_discard_successful_detection")){
            skipped++;Console.WriteLine("SKIP "+name+": would change ACLs on live user/global log folders");return;
        }
        try{action();passed++;Console.WriteLine("PASS "+name);}catch(Exception error){failed++;Console.WriteLine("FAIL "+name+": "+(error.InnerException??error).Message);}
    }
    static string Stem(string[] folders){return (string)typeof(RuntimeDependencies).GetMethod("LogStem",BindingFlags.Static|BindingFlags.NonPublic).Invoke(null,new object[]{"detect",folders});}
    static readonly BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
    static string[] LogFolders(){return new[]{Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AIPraat","logs","runtime"),Path.Combine(Path.GetTempPath(),"AIPraat","logs","runtime"),Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"logs","runtime")};}
    static void Restore(string folder,DirectorySecurity original)
    {
        var restored=new DirectorySecurity();
        restored.SetSecurityDescriptorSddlForm(original.GetSecurityDescriptorSddlForm(AccessControlSections.Access),AccessControlSections.Access);
        Directory.SetAccessControl(folder,restored);
    }
    sealed class DeniedFolders : IDisposable
    {
        readonly Dictionary<string,DirectorySecurity> originals=new Dictionary<string,DirectorySecurity>();
        public DeniedFolders(IEnumerable<string> folders)
        {
            try {foreach(string folder in folders.Distinct(StringComparer.OrdinalIgnoreCase)) {
                Directory.CreateDirectory(folder);var original=Directory.GetAccessControl(folder);originals.Add(folder,original);
                var denied=Directory.GetAccessControl(folder);denied.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,FileSystemRights.CreateFiles,AccessControlType.Deny));
                Directory.SetAccessControl(folder,denied);
                bool rejected=false;try{File.WriteAllText(Path.Combine(folder,"denial-fixture-"+Guid.NewGuid().ToString("N")),"fixture");}catch(UnauthorizedAccessException){rejected=true;}
                Assert(rejected,"fixture did not deny file creation: "+folder);
            }}catch {Dispose();throw;}
        }
        public void Dispose(){foreach(var pair in originals)Restore(pair.Key,pair.Value);originals.Clear();}
    }
    static void Wait(Func<bool> done)
    {
        var watch=System.Diagnostics.Stopwatch.StartNew();
        while(!done()&&watch.ElapsedMilliseconds<120000){Application.DoEvents();System.Threading.Thread.Sleep(20);}
        Assert(done(),"real asynchronous workflow timed out");
    }
    [STAThread] static int Main(string[] args)
    {
        try {return Run(args);}
        catch(Exception error){Console.WriteLine("FAIL test host: "+error.GetType().FullName+": "+error.Message);return 1;}
    }
    static int Run(string[] args)
    {
        scratchOnly=args.Contains("--scratch-only");
        string root=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"scratch","AIPraat-log-tests-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        Case("denied_log_file_creation_uses_writable_fallback",()=>{
            string blocked=Path.Combine(root,"blocked"),fallback=Path.Combine(root,"fallback");Directory.CreateDirectory(blocked);
            var original=Directory.GetAccessControl(blocked);var denied=Directory.GetAccessControl(blocked);
            denied.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,FileSystemRights.CreateFiles,AccessControlType.Deny));
            Directory.SetAccessControl(blocked,denied);
            try {
                bool rejected=false;try{File.WriteAllText(Path.Combine(blocked,"probe.log"),"probe");}catch(UnauthorizedAccessException){rejected=true;}
                Assert(rejected,"fixture did not reproduce access denied");
                string stem=Stem(new[]{blocked,fallback});Assert(Path.GetDirectoryName(stem)==fallback,"did not switch log directory");
                File.AppendAllText(stem+".log","detection result");File.WriteAllText(stem+".json","{}");
                Assert(File.ReadAllText(stem+".log")=="detection result"&&File.Exists(stem+".json"),"fallback cannot retain report/log");
            }finally{Restore(blocked,original);}
        });
        Case("all_unwritable_locations_return_no_log_stem",()=>{
            string blocked=Path.Combine(root,"file-instead-of-directory");File.WriteAllText(blocked,"fixture");
            Assert(Stem(new[]{blocked,blocked,blocked})=="","all denied log locations must not throw");
        });
        Case("temporary_work_files_fall_back_to_program_directory",()=>{
            string blocked=Path.Combine(root,"blocked-temp");File.WriteAllText(blocked,"fixture");
            string oldTemp=Environment.GetEnvironmentVariable("TEMP"),oldTmp=Environment.GetEnvironmentVariable("TMP"),working=null;
            try {
                Environment.SetEnvironmentVariable("TEMP",blocked);Environment.SetEnvironmentVariable("TMP",blocked);
                using(var assets=new RuntimeAssets()) {
                    working=assets.Folder;
                    Assert(Path.GetDirectoryName(working)==Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"ai","runtime")&&File.Exists(assets.Script),"working resources did not fall back to program directory");
                }
                Assert(!Directory.Exists(working),"fallback working resources were not cleaned up");
            }finally{Environment.SetEnvironmentVariable("TEMP",oldTemp);Environment.SetEnvironmentVariable("TMP",oldTmp);}
        });
        Case("denied_user_and_temp_logs_use_program_directory",()=>{
            using(var denied=new DeniedFolders(LogFolders().Take(2))) {
                var probe=RuntimeDependencies.Probe(args[0],"cpu",false,null);
                Assert(probe.Valid,probe.Message);
                Assert(Path.GetDirectoryName(probe.Report.log_path)==LogFolders()[2],"program directory fallback not used");
                Assert(File.Exists(probe.Report.log_path)&&File.Exists(Path.ChangeExtension(probe.Report.log_path,".json")),"fallback report/log missing");
            }
        });
        Case("all_logs_denied_real_probe_returns_complete_report",()=>{
            using(var denied=new DeniedFolders(LogFolders())) {
                var lines=new List<string>();var gate=new object();
                var probe=RuntimeDependencies.Probe(args[0],"cpu",false,line=>{lock(gate)lines.Add(line);});
                Assert(probe.Valid&&probe.Report.completed&&probe.Report.items.Length==15&&probe.Report.items.All(item=>item.ok),probe.Message);
                Assert(probe.Report.log_path==""&&probe.Message.Contains("日志未保存，不影响检测"),"no-log result/warning missing");
                Assert(lines.Count(line=>line.StartsWith("正在检测 "))==15,"real detection did not run exactly once");
                var invalid=RuntimeDependencies.Probe(args[0],"cuda",false,null);
                Assert(!invalid.Valid&&invalid.Report!=null&&invalid.Report.completed&&invalid.Report.items.Length==15&&invalid.Report.log_path=="","no-log mode hid real dependency failure");
            }
        });
        Case("denied_report_archive_does_not_discard_successful_detection",()=>{
            DeniedFolders denied=null;var lines=new List<string>();var gate=new object();
            try {
                var probe=RuntimeDependencies.Probe(args[0],"cpu",false,line=>{
                    lock(gate){lines.Add(line);if(denied==null&&line.StartsWith("所选 Python："))denied=new DeniedFolders(LogFolders());}
                });
                Assert(probe.Valid&&probe.Report.completed&&probe.Report.items.Length==15,probe.Message);
                Assert(File.Exists(probe.Report.log_path)&&!File.Exists(Path.ChangeExtension(probe.Report.log_path,".json")),"fixture did not deny archive only");
                Assert(lines.Any(line=>line.Contains("检测结果未保存，不影响检测")),"archive failure warning missing");
            }finally{if(denied!=null)denied.Dispose();}
        });
        Case("all_logs_denied_real_panel_opens_populated_runtime_window",()=>{
            using(var denied=new DeniedFolders(LogFolders())) {
                var settings=new PathSettings(Path.Combine(root,"ui/ai"),Path.Combine(root,"ui/ai/ai_config.json"),Path.Combine(root,"ui/profile"),args[0]);
                using(var host=new PathSettingsForm(settings,"",0))using(var monitor=new Timer{Interval=25}) {
                    host.Show();var panel=(DependencyPanel)host.Controls.Find("DependencyPanel",true)[0];Wait(()=>panel.Ready);
                    Assert(host.Controls.Find("RuntimePathStatus",true)[0].Text.Contains("15/15"),"runtime row still incomplete");
                    bool opened=false;Exception uiError=null;
                    monitor.Tick+=(s,e)=>{foreach(var window in Application.OpenForms.OfType<RuntimeDependenciesForm>().ToArray()) {
                        opened=true;if((bool)typeof(RuntimeDependenciesForm).GetField("busy",flags).GetValue(window))continue;
                        try {
                            Assert(window.Probe!=null&&window.Probe.Valid&&window.Probe.Report.log_path=="",window.Probe==null?"no report":window.Probe.Message);
                            var grid=(DataGridView)typeof(RuntimeDependenciesForm).GetField("grid",flags).GetValue(window);
                            Assert(grid.Rows.Count==15&&grid.Rows.Cast<DataGridViewRow>().All(row=>Convert.ToString(row.Cells[4].Value)!="待检测"),"runtime rows still empty");
                            var link=window.Controls.Find("OpenRuntimeLog",true)[0];Assert(link.Visible&&!link.Enabled,"no-log link must be disabled");
                            Console.WriteLine("REAL-WORKFLOW row="+host.Controls.Find("RuntimePathStatus",true)[0].Text+" rows="+grid.Rows.Count+" probeValid="+window.Probe.Valid+" log_path='"+window.Probe.Report.log_path+"'");
                        }catch(Exception error){uiError=error;}
                        monitor.Stop();window.Close();
                    }};
                    monitor.Start();((Button)host.Controls.Find("RuntimeDetails",true)[0]).PerformClick();
                    Assert(opened,"ConfigureRuntime did not open real window");if(uiError!=null)throw uiError;
                    Wait(()=>panel.Ready);host.Close();
                }
            }
        });
        Case("all_logs_denied_install_keeps_postinstall_report",()=>{
            using(var denied=new DeniedFolders(LogFolders())) {
                var before=RuntimeDependencies.Probe(args[0],"cpu",false,null);
                var result=RuntimeDependencies.Install(args[0],new RuntimeInstallPlan{device="cpu",before=before.Report},true,null);
                Assert(result.ExitCode==0&&result.Probe!=null&&result.Probe.Valid,result.Probe==null?"installation did not return report":result.Probe.Message);
                Assert(result.LogPath==""&&result.ResultPath==""&&result.Probe.Report.log_path==""&&result.Probe.Report.items.Length==15,"install lost no-log report or returned a deleted working file");
                var failedPlan=new RuntimeInstallPlan{device="cpu",before=before.Report};failedPlan.packages.Add("torch",Path.Combine(root,"missing.whl"));
                var failedResult=RuntimeDependencies.Install(args[0],failedPlan,false,null);
                Assert(failedResult.ExitCode!=0&&failedResult.Probe!=null&&!failedResult.Probe.Valid&&failedResult.Probe.Report!=null&&!string.IsNullOrEmpty(failedResult.Probe.Report.installation_error)&&failedResult.Probe.Report.log_path=="","no-log mode hid installation failure or discarded its report");
            }
        });
        Case("missing_report_is_detection_failure_not_15_missing_dependencies",()=>{
            using(var form=new Form())using(var panel=new DependencyPanel(false,root)) {
                form.Controls.Add(panel);panel.Fill(args[0],"","","","",new AlignmentPaths(),"cpu");
                var flags=BindingFlags.Instance|BindingFlags.NonPublic;
                typeof(DependencyPanel).GetField("pythonProbe",flags).SetValue(panel,new PythonProbe(false,"运行库检测失败：访问被拒绝。",true));
                var verified=(Dictionary<string,DependencyCandidate>)typeof(DependencyPanel).GetField("verified",flags).GetValue(panel);
                verified["python"]=new DependencyCandidate{Kind="python",Path=args[0]};
                typeof(DependencyPanel).GetMethod("RefreshState",flags).Invoke(panel,null);
                Assert(form.Controls.Find("RuntimePathStatus",true)[0].Text=="检测未完成"&&!panel.Ready,"log failure misreported as dependency count");
                Assert(form.Controls.Find("RuntimeDetails",true)[0].Enabled,"retry entry disabled after detection failure");
            }
        });
        Case("delivered_runtime_probe_retains_complete_report_and_log",()=>{
            var probe=RuntimeDependencies.Probe(args[0],"cpu",false,null);
            Assert(probe.Valid&&probe.Report.items.Length==15&&probe.Report.items.All(item=>item.ok),probe.Message);
            Assert(File.Exists(probe.Report.log_path)&&File.Exists(Path.ChangeExtension(probe.Report.log_path,".json")),"completed report/log missing");
            Console.WriteLine("Runtime log: "+probe.Report.log_path);
        });
        Console.WriteLine("RESULT "+passed+" passed, "+failed+" failed, "+skipped+" skipped; fixtures="+root);return failed==0?0:1;
    }
}
