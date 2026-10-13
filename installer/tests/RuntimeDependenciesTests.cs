using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Drawing;
using System.Windows.Forms;
using AIPraat.Setup;

static class RuntimeDependenciesTests
{
    static int failed;
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Case(string name,Action action){try{action();Console.WriteLine("PASS "+name);}catch(Exception e){failed++;Console.WriteLine("FAIL "+name+": "+(e.InnerException??e).Message);}}
    static readonly BindingFlags flags=BindingFlags.NonPublic|BindingFlags.Instance;
    [STAThread] static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        string python=args[0],missing=args[1],root=Path.Combine(Path.GetTempPath(),"AIPraat-runtime-ui-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        var ready=RuntimeDependencies.Probe(python,"cpu",true,null);
        Case("real_complete_environment_passes_all_13_without_wheels",()=>Assert(ready.Valid&&ready.Report.items.Length==13,ready.Message));
        Case("cuda_requires_actual_device",()=>{var probe=RuntimeDependencies.Probe(python,"cuda",false,null);Assert(!probe.Valid&&probe.Report.items.Single(x=>x.id=="torch").status.Contains("CUDA"),"CPU torch accepted as CUDA");});
        Case("embedded_manifest_and_scripts_match_source",()=>{
            Assert(RuntimeDependencies.Items().Length==13,"missing item");
            using(var assets=new RuntimeAssets())Assert(File.Exists(assets.Script)&&File.ReadAllText(assets.Requirements).Contains("-r requirements-wav2vec2.txt"),"missing embedded resources");
        });
        Case("online_ui_reuses_postinstall_report_and_keeps_timestamped_logs",()=>{
            using(var form=new RuntimeDependenciesForm(python,"cpu")) {
                typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,true);form.Show();Application.DoEvents();
                typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,false);
                typeof(RuntimeDependenciesForm).GetProperty("Probe").SetValue(form,ready,null);
                typeof(RuntimeDependenciesForm).GetMethod("Run",flags).Invoke(form,new object[]{true});
                var wait=System.Diagnostics.Stopwatch.StartNew();
                while((bool)typeof(RuntimeDependenciesForm).GetField("busy",flags).GetValue(form)&&wait.ElapsedMilliseconds<120000){Application.DoEvents();System.Threading.Thread.Sleep(20);}
                Assert(form.Probe!=null&&form.Probe.Valid,form.Probe==null?"UI did not finish":form.Probe.Message);
                var text=form.Controls.Find("RuntimeLog",true)[0].Text;
                Assert(System.Text.RegularExpressions.Regex.Matches(text,"正在检测 ").Count==13,"UI repeated full detection");
                Assert(!text.Contains("pip install "),"complete environment invoked pip install");
                string path=form.Probe.Report.log_path,result=Path.ChangeExtension(path,".json");
                Assert(File.Exists(path)&&File.Exists(result)&&File.ReadAllText(path).Contains("所选 Python"),"logs/results removed with temp resources");
                Assert(File.ReadAllText(path).Contains("全部必需运行库已通过复检和新前端窗口启动检查。"),"Chinese transcript was not saved as UTF-8");
                var report=new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<RuntimeReport>(File.ReadAllText(result));
                Assert(report.checked_at==form.Probe.Report.checked_at&&report.smoke,"UI did not reuse PowerShell's exact report");
                Assert(form.Controls.Find("OpenRuntimeLog",true)[0].Visible,"saved log link missing");
                Assert(!RuntimeDependencies.ReadProbe(result,missing,"cpu",0).Valid&&!RuntimeDependencies.ReadProbe(result,python,"cuda",0).Valid,"result for other Python/device accepted");
                form.Close();
            }
        });
        Case("failed_install_returns_one_full_report_and_persistent_error_log",()=>{
            var plan=new RuntimeInstallPlan{device="cpu",before=ready.Report};plan.packages.Add("torch",Path.Combine(root,"missing.whl"));
            var lines=new System.Collections.Generic.List<string>();var gate=new object();
            var result=RuntimeDependencies.Install(python,plan,false,line=>{lock(gate)lines.Add(line);});
            Assert(result.ExitCode!=0&&!result.Probe.Valid&&!string.IsNullOrEmpty(result.Probe.Report.installation_error),"failed installation became ready");
            Assert(lines.Count(x=>x.StartsWith("正在检测 "))==13,"failed install repeated full detection");
            Assert(File.Exists(result.LogPath)&&File.Exists(result.ResultPath)&&File.ReadAllText(result.LogPath).Contains("missing.whl"),"failure log lost");
        });
        Case("both_hosts_use_shared_runtime_field_and_switch_clears_ready",()=>{
            var settings=new PathSettings(Path.Combine(root,"ai"),Path.Combine(root,"ai/ai_config.json"),Path.Combine(root,"profile"),python);
            using(var form=new PathSettingsForm(settings,"",0)) {
                form.Show();var wait=System.Diagnostics.Stopwatch.StartNew();
                while(!form.Controls.Find("SavePaths",true)[0].Enabled&&wait.ElapsedMilliseconds<90000){Application.DoEvents();System.Threading.Thread.Sleep(20);}
                var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];
                Assert(form.Controls.Find("RuntimeDetails",true).Length==1,"path entry missing");
                Assert(form.Controls.Find("SavePaths",true)[0].Enabled,"complete env cannot save");
                form.Controls.Find("PythonPath",true)[0].Text=missing;
                Assert(!form.Controls.Find("SavePaths",true)[0].Enabled&&typeof(DependencyPanel).GetField("pythonProbe",flags).GetValue(panel)==null,"old readiness persisted");form.Close();
            }
            using(var form=new WizardForm(new InstallRequest{InstallDirectory=Path.Combine(root,"install")},()=>null)) {
                form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();
                Assert(form.Controls.Find("SkipPrerequisites",true).Length==0,"skip still exists");
                Assert(form.Controls.Find("RuntimeDetails",true).Length==1&&!form.Controls.Find("Next",true)[0].Enabled,"requirements can be skipped");
                form.Close();
            }
        });
        Case("shared_dialog_contains_13_rows_and_offline_controls",()=>{
            using(var form=new RuntimeDependenciesForm(python,"cpu")) {
                typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,true);
                form.Show();Application.DoEvents();typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,false);
                typeof(RuntimeDependenciesForm).GetProperty("Probe").SetValue(form,ready,null);
                typeof(RuntimeDependenciesForm).GetMethod("UpdateRows",flags).Invoke(form,null);
                var grid=(DataGridView)typeof(RuntimeDependenciesForm).GetField("grid",flags).GetValue(form);
                Assert(grid.Rows.Count==13&&form.Controls.Find("DependencyFolder",true).Length==1,"missing local rows");
                foreach(string name in new[]{"InstallLocalRuntime","InstallOnlineRuntime","DetectRuntime"})Assert(form.Controls.Find(name,true).Length==1,"missing "+name);
                using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"runtime-dialog.png"));}
                form.Close();
            }
        });
        Case("save_rechecks_selected_environment_and_synchronizes_cpu",()=>{
            string ai=Path.Combine(root,"save/ai"),config=Path.Combine(ai,"ai_config.json");Directory.CreateDirectory(ai);
            File.WriteAllText(config,"{\"alignment\":{\"wav2vec2\":{\"device\":\"cuda\",\"model\":\"keep/model\",\"enabled\":true}},\"api\":{\"api_key\":\"keep\"}}");
            var settings=new PathSettings(ai,config,Path.Combine(root,"save/profile"),python);
            settings.SavePaths(python,"","","",null,"cpu");string saved=File.ReadAllText(config);
            Assert(saved.Contains("\"device\":\"cpu\"")&&saved.Contains("keep/model")&&saved.Contains("keep"),"CPU or model settings lost");
            bool blocked=false;try{settings.SavePaths(missing,"","","",null,"cpu");}catch(IOException){blocked=true;}
            Assert(blocked&&File.ReadAllText(config)==saved,"save trusted stale environment");
        });
        Case("installer_engine_rechecks_and_rejects_skip_before_writing",()=>{
            var request=new InstallRequest{InstallDirectory=Path.Combine(root,"blocked"),PythonPath=missing,ConfigurePrerequisites=true};
            bool blocked=false;try{new InstallEngine().Run(request,null,null);}catch(IOException){blocked=true;}
            Assert(blocked&&!Directory.Exists(request.InstallDirectory),"missing environment installed");
            request.PythonPath=python;request.ConfigurePrerequisites=false;blocked=false;
            try{new InstallEngine().Run(request,null,null);}catch(IOException){blocked=true;}
            Assert(blocked,"skip accepted");
        });
        Console.WriteLine("Screenshots: "+root);return failed==0?0:1;
    }
}
