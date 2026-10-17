using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using AIPraat.Setup;

// Alignment-environment detection in the path dialog. The probe itself is covered by
// ai/tests/test_mfa_alignment_probe.py; these cases check the host wiring and wording.
static class MfaAlignmentProbeTests
{
    static int failed;
    static readonly BindingFlags flags=BindingFlags.NonPublic|BindingFlags.Instance;
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Case(string name,Action action){try{action();Console.WriteLine("PASS "+name);}catch(Exception e){failed++;Console.WriteLine("FAIL "+name+": "+(e.InnerException??e).GetType().FullName+" / "+(e.InnerException??e).Message);}Console.Out.Flush();}
    static Dictionary<string,DependencyRow> Rows(DependencyPanel panel){return (Dictionary<string,DependencyRow>)typeof(DependencyPanel).GetField("rows",flags).GetValue(panel);}
    static Dictionary<string,DependencyCandidate> Verified(DependencyPanel panel){return (Dictionary<string,DependencyCandidate>)typeof(DependencyPanel).GetField("verified",flags).GetValue(panel);}
    static MfaAlignmentReport Report(DependencyPanel panel){return (MfaAlignmentReport)typeof(DependencyPanel).GetField("alignmentReport",flags).GetValue(panel);}
    static void Check(DependencyPanel panel,string kind){typeof(DependencyPanel).GetMethod("Check",flags).Invoke(panel,new object[]{kind});}
    static bool Wait(Func<bool> condition,int milliseconds)
    {
        var watch=System.Diagnostics.Stopwatch.StartNew();
        while(watch.ElapsedMilliseconds<milliseconds){Application.DoEvents();if(condition())return true;System.Threading.Thread.Sleep(20);}
        return condition();
    }
    static string Sample(bool ready)
    {
        string openfst=ready
            ? "{\"id\":\"openfst\",\"label\":\"openfst（fstcompile）\",\"status\":\"ok\",\"detail\":\"C:\\\\envs\\\\aligner\\\\Library\\\\bin\\\\fstcompile.exe\",\"repair\":\"\"}"
            : "{\"id\":\"openfst\",\"label\":\"openfst（fstcompile）\",\"status\":\"error\",\"detail\":\"RuntimeError: fstcompile 不在 PATH\",\"repair\":\"改用 conda run -n aligner mfa\"}";
        return "{\"schema\":\"mfa-alignment-probe-v1\",\"ready\":"+(ready?"true":"false")+
            ",\"summary\":\"summary text\",\"launcher\":\"conda run -n aligner mfa\",\"environment\":\"aligner\","+
            "\"version\":\"3.4.2\",\"blocking\":["+(ready?"": "\"openfst\"")+"],\"checks\":["+
            "{\"id\":\"launcher\",\"label\":\"MFA 启动方式\",\"status\":\"ok\",\"detail\":\"3.4.2\",\"repair\":\"\"},"+openfst+"]}";
    }
    [STAThread] static int Main(string[] args)
    {
        try { return Run(args); }
        catch(Exception error)
        {
            // The CLR's own "unhandled exception" printer fails on this console, so the
            // type name is reported here instead of losing the failure entirely.
            Console.WriteLine("HOST CRASH "+error.GetType().FullName+" / "+error.Message);
            Console.Out.Flush();
            return 2;
        }
    }
    static int Run(string[] args)
    {
        Console.WriteLine("START alignment probe suite");Console.Out.Flush();
        Application.EnableVisualStyles();
        string python=args[0];
        string root=Path.Combine(Path.GetTempPath(),"AIPraat-mfa-ui-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        Case("probe_report_parses_and_describes_every_item",()=>{
            var report=new JavaScriptSerializer().Deserialize<MfaAlignmentReport>(Sample(false));
            Assert(!report.ready,"blocking report marked ready");
            Assert(report.blocking.Length==1&&report.blocking[0]=="openfst","blocking ids lost");
            var described=report.Describe();
            foreach(string expected in new[]{"[可用] MFA 启动方式","[阻塞] openfst","修复：改用 conda run","3.4.2"})
                Assert(described.Contains(expected),"description missing: "+expected);
            Assert(report.FirstBlockingReason().Contains("fstcompile"),"first blocking reason wrong");
            var ok=new JavaScriptSerializer().Deserialize<MfaAlignmentReport>(Sample(true));
            Assert(ok.ready&&ok.FirstBlockingReason()=="","ready report should have no blocking reason");
        });
        Case("missing_probe_result_is_not_reported_as_healthy",()=>{
            var report=new MfaAlignmentReport{Message="需要先选择可用的 Python 环境，才能检测对齐环境。"};
            Assert(!report.ready,"empty report marked ready");
            Assert(report.Describe().Contains("Python"),"message lost");
        });
        Case("alignment_paths_round_trip_preserves_conda_launcher",()=>{
            var mfa=new Dictionary<string,object>{{"executable","mfa"},{"conda_executable",@"C:\miniforge3\Scripts\conda.exe"},{"conda_environment","aligner"}};
            var alignment=new Dictionary<string,object>{{"mfa",mfa}};
            var config=new Dictionary<string,object>{{"alignment",alignment}};
            var paths=AlignmentPaths.Read(config);
            Assert(paths.MfaCondaExecutable.EndsWith("conda.exe")&&paths.MfaCondaEnvironment=="aligner","conda launcher not read");
            paths.Apply(config,paths);
            var again=AlignmentPaths.Read(config);
            Assert(again.MfaCondaExecutable==paths.MfaCondaExecutable&&again.MfaCondaEnvironment=="aligner","unchanged conda launcher dropped on save");
            paths.MfaCondaEnvironment="other";paths.Apply(config,AlignmentPaths.Read(config));
            Assert(AlignmentPaths.Read(config).MfaCondaEnvironment=="other","changed conda environment not written");
        });
        Case("panel_keeps_mfa_unverified_when_nothing_is_configured",()=>{
            using(var panel=new DependencyPanel(false,root)) {
                panel.Fill(python,"","","","",new AlignmentPaths{MfaExecutablePath=""},"cpu");
                Verified(panel)["python"]=new DependencyCandidate{Kind="python",Path=python};
                Rows(panel)["python"].PathBox.Text=python;
                Check(panel,"mfa");
                Assert(Wait(()=>Report(panel)!=null,180000),"unconfigured MFA produced no report");
                Assert(Rows(panel)["mfa"].Status.Text=="未配置",Rows(panel)["mfa"].Status.Text);
                var report=Report(panel);
                Assert(!report.ready&&report.checks.Any(x=>x.id=="launcher"&&!x.ok),"missing launcher not reported");
                Assert(report.checks.Any(x=>x.id=="acoustic"&&x.repair.Contains("语言与音位资源")),"repair hint missing");
                var link=(LinkLabel)typeof(DependencyPanel).GetField("alignmentDetails",flags).GetValue(panel);
                // The entry lives inside the optional-tools fold, so a user sees it exactly
                // when that group (which contains the MFA row) is open.
                typeof(DependencyPanel).GetMethod("SetFold",flags).Invoke(panel,new object[]{(Panel)((Control)Rows(panel)["mfa"]).Parent,true});
                Assert(link.Visible,"detail entry hidden while MFA is unconfigured");
                Assert(((Control)Rows(panel)["mfa"]).Parent==link.Parent,"detail entry is not next to the MFA row");
                var feedback=(Label)typeof(DependencyRow).GetField("feedback",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(Rows(panel)["mfa"]);
                Assert(feedback.Text=="","unconfigured optional tool must not nag: "+feedback.Text);
            }
        });
        Case("panel_renders_the_alignment_entry_under_the_mfa_row",()=>{
            string scripts=Path.Combine(root,"Scripts");
            string dictionary=Path.Combine(root,"japanese_mfa.dict");
            using(var panel=new DependencyPanel(false,root)) {
                panel.Fill(python,"","","","",new AlignmentPaths{MfaExecutablePath=Path.Combine(scripts,"mfa.bat"),MfaDictionaryPath=dictionary},"cpu");
                Verified(panel)["python"]=new DependencyCandidate{Kind="python",Path=python};
                Rows(panel)["python"].PathBox.Text=python;
                Panel tools=(Panel)Rows(panel)["mfa"].Parent;
                // Inject the report instead of running the real probe: this case is about
                // where the entry is rendered, not about detection.
                var report=new JavaScriptSerializer().Deserialize<MfaAlignmentReport>(Sample(false));
                typeof(DependencyPanel).GetField("alignmentReport",flags).SetValue(panel,report);
                typeof(DependencyPanel).GetMethod("SetFold",flags).Invoke(panel,new object[]{tools,true});
                typeof(DependencyPanel).GetMethod("RefreshState",flags).Invoke(panel,null);
                var link=(LinkLabel)typeof(DependencyPanel).GetField("alignmentDetails",flags).GetValue(panel);
                Assert(link.Visible,"detail entry hidden");
                Assert(link.Parent==tools,"detail entry is not inside the optional-tools group");
                int row=((TableLayoutPanel)tools).GetRow(link),mfaRow=((TableLayoutPanel)tools).GetRow(Rows(panel)["mfa"]);
                Assert(row>mfaRow&&row-mfaRow<=2,"detail entry is not directly under the MFA row: "+mfaRow+"->"+row);
                Assert(link.Text.Contains("对齐环境明细"),"unexpected entry text: "+link.Text);
            }
        });
        Case("panel_flags_a_broken_alignment_environment",()=>{
            string scripts=Path.Combine(root,"Scripts");Directory.CreateDirectory(scripts);
            string fake=Path.Combine(scripts,"mfa.bat");
            File.WriteAllText(fake,"@echo 3.4.2\r\n");
            string dictionary=Path.Combine(root,"japanese_mfa.dict");
            File.WriteAllText(dictionary,"あなた\ta n a t a\n");
            using(var panel=new DependencyPanel(false,root)) {
                panel.Fill(python,"","","","",new AlignmentPaths{MfaExecutablePath=fake,MfaDictionaryPath=dictionary},"cpu");
                Verified(panel)["python"]=new DependencyCandidate{Kind="python",Path=python};
                Rows(panel)["python"].PathBox.Text=python;
                Check(panel,"mfa");
                Assert(Wait(()=>Report(panel)!=null,180000),"alignment probe never returned");
                var report=Report(panel);
                Assert(!report.ready,"broken environment accepted");
                Assert(report.checks.Any(x=>x.id=="acoustic"&&!x.ok),"missing acoustic model not reported");
                Assert(Rows(panel)["mfa"].Status.Text=="对齐环境不完整",Rows(panel)["mfa"].Status.Text);
                var feedback=(Label)typeof(DependencyRow).GetField("feedback",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(Rows(panel)["mfa"]);
                Assert(feedback.Text.Contains("对齐环境不足以运行对齐"),"blocking reason not shown: "+feedback.Text);
                var link=(LinkLabel)typeof(DependencyPanel).GetField("alignmentDetails",flags).GetValue(panel);
                Assert(link.Visible,"alignment detail link hidden");
                Assert(report.Describe().Contains("修复："),"repair hints missing");
            }
        });
        Case("alignment_dialog_edits_the_launcher_and_redetects",()=>{
            string dictionary=Path.Combine(root,"japanese_mfa.dict");
            if(!File.Exists(dictionary))File.WriteAllText(dictionary,"あなた\ta n a t a\n");
            string fake=Path.Combine(root,"Scripts","mfa.bat");
            using(var panel=new DependencyPanel(false,root))
            using(var timer=new Timer{Interval=50}) {
                // Fill before the panel loads: that avoids starting the slow Python and
                // FFmpeg probes, while the MFA probe is still exercised for real.
                panel.Fill(python,"","","","",new AlignmentPaths{MfaExecutablePath=fake,MfaDictionaryPath=dictionary},"cpu");
                Verified(panel)["python"]=new DependencyCandidate{Kind="python",Path=python};
                Rows(panel)["python"].PathBox.Text=python;
                Check(panel,"mfa");
                Assert(Wait(()=>Report(panel)!=null,180000),"alignment probe never returned");
                var before=Report(panel);
                bool opened=false,clicked=false;int settle=0;string problem=null;
                timer.Tick+=(s,e)=>{
                    var dialog=Application.OpenForms.Cast<Form>().FirstOrDefault(f=>f.Text=="MFA 对齐环境明细");
                    if(dialog==null||!dialog.IsHandleCreated)return;
                    // Let the dialog finish creating its child handles before interacting:
                    // clicking during CreateHandle() makes disposal of the form fail.
                    if(++settle<3)return;
                    timer.Stop();
                    try {
                        var conda=dialog.Controls.Find("MfaCondaExecutablePath",true);
                        var environment=dialog.Controls.Find("MfaCondaEnvironmentName",true);
                        var retry=dialog.Controls.Find("RetryAlignmentEnvironment",true);
                        Assert(conda.Length==1&&environment.Length==1&&retry.Length==1,"launcher fields missing");
                        try {
                            using(var bitmap=new Bitmap(dialog.Width,dialog.Height)) {
                                dialog.DrawToBitmap(bitmap,new Rectangle(Point.Empty,dialog.Size));
                                bitmap.Save(Path.Combine(root,"mfa-alignment-dialog.png"));
                            }
                        } catch(Exception) {}   // visual record only; never fails the case
                        ((TextBox)conda[0]).Text=Path.Combine(root,"conda.exe");
                        ((TextBox)environment[0]).Text="aligner";
                        opened=true;
                        clicked=true;
                        // DialogResult=OK ends the modal loop; the panel then applies the
                        // edited launcher and re-runs the probe.
                        ((Button)retry[0]).PerformClick();
                    }catch(Exception failure){problem=failure.Message;}
                    finally {if(!clicked&&!dialog.IsDisposed){try{if(dialog.Visible)dialog.Close();}catch(Exception){}}}
                };
                timer.Start();typeof(DependencyPanel).GetMethod("ShowAlignmentDetails",flags).Invoke(panel,null);timer.Stop();
                Assert(opened,problem??"alignment dialog never opened");
                Assert(panel.MfaCondaExecutable.EndsWith("conda.exe")&&panel.MfaCondaEnvironment=="aligner","launcher edit not kept");
                Assert(panel.Alignment.MfaCondaEnvironment=="aligner","conda environment not exposed for saving");
                Check(panel,"mfa");
                Assert(Wait(()=>Report(panel)!=null&&!object.ReferenceEquals(Report(panel),before)
                    &&Report(panel).checks.Any(x=>x.id=="launcher"&&!x.ok),180000),
                    "re-detect did not report the conda launcher problem");
                Assert(Report(panel).checks.Any(x=>x.id=="launcher"&&!x.ok),"conda launcher failure not reported");
            }
        });
        Console.WriteLine("Screenshots: "+root);return failed==0?0:1;
    }
}
