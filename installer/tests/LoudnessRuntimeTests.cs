using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using AIPraat.Setup;

static class LoudnessRuntimeTests
{
    static int failed;
    static readonly BindingFlags flags=BindingFlags.NonPublic|BindingFlags.Instance;
    static void Assert(bool ok,string text){if(!ok)throw new Exception(text);}
    static void Case(string name,Action test){try{test();Console.WriteLine("PASS "+name);}catch(Exception e){failed++;Console.WriteLine("FAIL "+name+": "+e.Message);}}
    static RuntimeReport Ready(string python){return new RuntimeReport{python=python,device="cpu",version="3.11",supported=true,completed=true,ready=true,items=RuntimeDependencies.Items().Select(x=>new RuntimeItem{id=x.id,label=x.label,requirement=x.requirement,required=x.required,ok=true,status="已就绪",installed=x.id=="scipy"?"1.14.1":x.id=="pyloudnorm"?"0.2.0":"fixture"}).ToArray()};}
    [STAThread] static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        string root=args[0],python=args[1],host=args[2];
        string temp=Path.Combine(root,"scratch","normalization-runtime","csharp-"+host);Directory.CreateDirectory(temp);
        Console.WriteLine("Verification host: "+host);
        Case("actual_host_compiled",()=>Assert(Assembly.GetExecutingAssembly().GetType(host=="wizard"?"InstallerProgram":"PathSettingsProgram")!=null,"host entry source missing"));
        Case("all_required_entries_labels_versions_and_no_numpy_duplicate",()=>{
            var rows=RuntimeDependencies.Items();
            Assert(rows.Length==15&&rows.All(x=>x.required),"incomplete required manifest");
            Assert(rows.Single(x=>x.id=="scipy").label=="SciPy（响度滤波）","SciPy label");
            Assert(rows.Single(x=>x.id=="pyloudnorm").label=="pyloudnorm（响度归一化）","pyloudnorm label");
            Assert(rows.Single(x=>x.id=="scipy").requirement=="scipy>=1.14.1,<1.15"&&rows.Single(x=>x.id=="pyloudnorm").requirement=="pyloudnorm==0.2.0","constraints");
            Assert(rows.Count(x=>x.id=="numpy")==1,"duplicated NumPy");
        });
        Case("both_hosts_embed_exact_source_and_extract_license",()=>{
            string[] names={"runtime_dependencies.py","requirements.txt","requirements-wav2vec2.txt","ConfigurePython.ps1","pyloudnorm-LICENSE.txt","mfa_alignment_probe.py"};
            string[] sources={"ai/runtime_dependencies.py","ai/requirements.txt","ai/requirements-wav2vec2.txt","installer/src/ConfigurePython.ps1","ai/third_party/pyloudnorm/LICENSE","ai/mfa_alignment_probe.py"};
            using(var assets=new RuntimeAssets())for(int i=0;i<names.Length;i++){
                string expected=File.ReadAllText(Path.Combine(root,sources[i]));
                Assert(RuntimeDependencies.Resource(names[i])==expected,"embedded stale "+names[i]);
                Assert(File.ReadAllText(Path.Combine(assets.Folder,names[i]))==expected,"extracted stale "+names[i]);
            }
        });
        Case("old_duplicate_missing_changed_or_optional_result_never_ready",()=>{
            var ready=Ready(python);Assert(ready.ToProbe().Valid,"fixture not ready");
            ready.items=ready.items.Where(x=>x.id!="scipy"&&x.id!="pyloudnorm").ToArray();Assert(!ready.ToProbe().Valid,"old 13 rows accepted");
            ready=Ready(python);ready.items[1]=ready.items[0];Assert(!ready.ToProbe().Valid,"duplicate rows accepted");
            ready=Ready(python);ready.items[1].requirement="scipy>=1";Assert(!ready.ToProbe().Valid,"stale version accepted");
            ready=Ready(python);ready.items[1].required=false;Assert(!ready.ToProbe().Valid,"optional item accepted");
            ready=Ready(python);ready.items[1]=null;Assert(!ready.ToProbe().Valid,"null item accepted");
        });
        Case("serialized_old_results_explicitly_request_redetection",()=>{
            string file=Path.Combine(temp,"old.json");var ready=Ready(python);ready.items=ready.items.Where(x=>x.id!="scipy"&&x.id!="pyloudnorm").ToArray();
            File.WriteAllText(file,new JavaScriptSerializer().Serialize(ready));
            var probe=RuntimeDependencies.ReadProbe(file,python,"cpu",0);Assert(!probe.Valid&&probe.Message.Contains("重新检测"),"old result reused");
            ready=Ready(python);File.WriteAllText(file,new JavaScriptSerializer().Serialize(ready));Assert(RuntimeDependencies.ReadProbe(file,python,"cpu",0).Valid,"current complete result rejected");
            Assert(!RuntimeDependencies.ReadProbe(file,python,"cpu",1).Valid,"nonzero exit became ready");
        });
        Case("new_function_failure_retains_installed_version_and_failed_row",()=>{
            var report=Ready(python);var row=report.items.Single(x=>x.id=="scipy");row.ok=false;row.status="加载/功能检测失败：ABI";
            Assert(!report.ToProbe().Valid&&report.ToProbe().Message.Contains("SciPy"),"failed required item accepted");
            Assert(row.installed=="1.14.1","installed version lost");
        });
        Case("dialog_new_rows_local_wheels_scroll_and_actions",()=>{
            var plan=new RuntimeInstallPlan();plan.packages.Add("pyloudnorm",Path.Combine(temp,"pyloudnorm-0.2.0-py3-none-any.whl"));
            using(var form=new RuntimeDependenciesForm(python,"cpu",plan)){
                typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,true);form.Show();Application.DoEvents();
                typeof(RuntimeDependenciesForm).GetField("busy",flags).SetValue(form,false);
                var report=Ready(python);report.items.Single(x=>x.id=="scipy").ok=false;report.items.Single(x=>x.id=="scipy").status="加载/功能检测失败：ABI";
                typeof(RuntimeDependenciesForm).GetProperty("Probe").SetValue(form,report.ToProbe(),null);
                typeof(RuntimeDependenciesForm).GetMethod("UpdateRows",flags).Invoke(form,null);
                var grid=(DataGridView)form.Controls.Find("RuntimeItems",true)[0];
                Assert(grid.Rows.Count==15&&grid.ScrollBars==ScrollBars.Both,"new rows/scroll missing");
                int scipy=Array.FindIndex(RuntimeDependencies.Items(),x=>x.id=="scipy"),loud=Array.FindIndex(RuntimeDependencies.Items(),x=>x.id=="pyloudnorm");
                Assert(Convert.ToString(grid.Rows[scipy].Cells[3].Value)=="1.14.1"&&Convert.ToString(grid.Rows[scipy].Cells[4].Value).Contains("失败"),"failed row hidden");
                Assert(Convert.ToString(grid.Rows[loud].Cells[5].Value)==plan.packages["pyloudnorm"]&&Convert.ToString(grid.Rows[loud].Cells[6].Value)=="浏览…","local selection missing");
                grid.FirstDisplayedScrollingRowIndex=14;Application.DoEvents();Assert(grid.Rows[14].Displayed,"last required row unreachable");
                foreach(string name in new[]{"DependencyFolder","BrowseDependencyFolder","InstallLocalRuntime","InstallOnlineRuntime","DetectRuntime","OpenRuntimeLog"})Assert(form.Controls.Find(name,true).Length==1,"missing "+name);
                form.Close();
            }
        });
        return failed==0?0:1;
    }
}
