using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Drawing;
using System.Windows.Forms;

static class PathWindowProductionTests
{
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Wait(Func<bool> done)
    {
        var watch=System.Diagnostics.Stopwatch.StartNew();
        while(!done()&&watch.ElapsedMilliseconds<120000){Application.DoEvents();System.Threading.Thread.Sleep(20);}
        Assert(done(),"real window detection timed out");
    }
    [STAThread] static int Main(string[] args)
    {
        try {
            Application.EnableVisualStyles();
            string root=AppDomain.CurrentDomain.BaseDirectory,fixture=Path.Combine(root,"scratch","path-window-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(fixture);
            Directory.CreateDirectory(Path.Combine(fixture,"ai"));File.WriteAllText(Path.Combine(fixture,"ai","ai_config.json"),"{\"alignment\":{\"wav2vec2\":{\"model\":\"org/managed\",\"device\":\"cpu\"}}}");
            var assembly=Assembly.Load(File.ReadAllBytes(Path.Combine(root,"AIPraat-paths.exe")));
            var settings=Activator.CreateInstance(assembly.GetType("AIPraat.Setup.PathSettings"),new object[]{Path.Combine(fixture,"ai"),Path.Combine(fixture,"ai","ai_config.json"),Path.Combine(fixture,"profile"),args[0]});
            using(var host=(Form)Activator.CreateInstance(assembly.GetType("AIPraat.Setup.PathSettingsForm"),new object[]{settings,"",0})) {
                host.Show();var panel=(UserControl)host.Controls.Find("DependencyPanel",true)[0];
                foreach(string name in new[]{"ModelPath","ModelPathStatus","BrowseModelPath","Wav2Vec2ModelPath","Wav2Vec2ModelPathStatus","BrowseWav2Vec2ModelPath","RuntimePath","ConfigureRuntime"})Assert(host.Controls.Find(name,true).Length==0,"ghost model control remains: "+name);
                var mmproj=host.Controls.Find("MmprojPath",true)[0];var mfa=host.Controls.Find("MfaExecutablePath",true)[0];
                Console.WriteLine("PASS delivered root path window has no language model row or gap");
                var flags=BindingFlags.Instance|BindingFlags.NonPublic;
                var probeField=panel.GetType().GetField("pythonProbe",flags);
                Wait(()=>probeField.GetValue(panel)!=null&&host.Controls.Find("PythonPathStatus",true)[0].Text!="检测中…");
                var probe=probeField.GetValue(panel);bool valid=(bool)probe.GetType().GetProperty("Valid").GetValue(probe,null);
                string message=(string)probe.GetType().GetProperty("Message").GetValue(probe,null);
                string validation=(string)panel.GetType().GetProperty("ValidationMessage").GetValue(panel,null);
                Console.WriteLine("REAL-WORKFLOW runtime="+host.Controls.Find("RuntimePathStatus",true)[0].Text+" probeValid="+valid+" validation="+validation);
                var ffmpeg=panel.GetType().GetField("ffmpegProbe",flags).GetValue(panel);
                if(ffmpeg!=null)Console.WriteLine("FFmpeg: "+ffmpeg.GetType().GetField("Message").GetValue(ffmpeg));
                var fold=(LinkLabel)host.Controls.Find("OptionalTools",true)[0];
                if(fold.Text.StartsWith("▸"))typeof(LinkLabel).GetMethod("OnLinkClicked",flags).Invoke(fold,new object[]{new LinkLabelLinkClickedEventArgs(fold.Links[0])});
                panel.ScrollControlIntoView(mmproj.Parent.Parent);Application.DoEvents();
                Assert(mmproj.Parent.Top==mfa.Parent.Bottom,"removed model rows left an empty slot");
                using(var bitmap=new Bitmap(host.Width,host.Height)) {
                    host.DrawToBitmap(bitmap,new Rectangle(Point.Empty,host.Size));bitmap.Save(Path.Combine(root,"test-records","installer","delivered-paths-designed.png"));
                }
                Assert(valid,message);
                Assert((bool)panel.GetType().GetProperty("Ready").GetValue(panel,null),validation);
                Console.WriteLine("PASS delivered root path window completes real asynchronous runtime and FFmpeg detection");
                var before=host.Size;host.Size=host.MinimumSize;Application.DoEvents();Assert(!panel.HorizontalScroll.Visible,"narrow path window has horizontal overflow");using(var bitmap=new Bitmap(host.Width,host.Height)){host.DrawToBitmap(bitmap,new Rectangle(Point.Empty,host.Size));bitmap.Save(Path.Combine(root,"test-records","installer","delivered-paths-designed-small.png"));}host.Size=before;
                ((Button)host.Controls.Find("SavePaths",true)[0]).PerformClick();Wait(()=>host.IsDisposed||host.Controls.Find("PathValidationMessage",true)[0].Text.StartsWith("保存失败"));
                if(!host.IsDisposed)throw new Exception("real save failed: "+host.Controls.Find("PathValidationMessage",true)[0].Text);
                Assert(host.DialogResult==DialogResult.OK,"real path save did not complete");Assert(File.ReadAllText(Path.Combine(fixture,"ai","ai_config.json")).Contains("\"model\":\"org/managed\""),"removed wav2vec2 row lost existing model setting");
                Console.WriteLine("PASS real path save preserves the managed wav2vec2 setting");
            }
            return 0;
        }catch(Exception error){Console.WriteLine("FAIL "+(error.InnerException??error).Message);return 1;}
    }
}
