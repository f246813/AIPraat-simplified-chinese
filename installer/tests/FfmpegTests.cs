using System;
using System.IO;
using System.Windows.Forms;
using AIPraat.Setup;

public static class FfmpegTests
{
    static void Assert(bool ok,string message) {if(!ok)throw new Exception(message);}
    [STAThread]
    public static int Main(string[] args)
    {
        try {
            if(args.Length>3&&args[3]=="install") {
                var installed=FfmpegDependencies.Install(Console.WriteLine);
                Assert(installed.Ready,installed.Message);
                Console.WriteLine("PASS one-click installer backend + post-install actual decode");
            }
            Assert(!FfmpegDependencies.Probe(@"Z:\missing-ffmpeg.exe").Ready,"Missing executable passed");
            var good=FfmpegDependencies.Probe(args[0]);
            Assert(good.Ready,good.Message);
            Console.WriteLine("PASS executable + real Ogg decode");
            string root=Path.Combine(Path.GetTempPath(),"aipraat-ffmpeg-tests-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root,"ai"));
            File.WriteAllText(Path.Combine(root,"ai","ai_config.json"),"{\"audio\":{\"ffmpeg\":"+new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(args[0])+"}}");
            var settings=new PathSettings(Path.Combine(root,"ai"),Path.Combine(root,"ai","ai_config.json"),Path.Combine(root,"profile"),"");
            Assert(settings.FfmpegPath==args[0],"Shared path not loaded");
            settings.SavePaths(args[2],"","","",null,"cpu",args[0]);
            var saved=new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"ai","ai_config.json")));
            Assert(Convert.ToString(Configuration.Object(saved,"audio")["ffmpeg"])==args[0],"Shared decoder path not saved");
            Console.WriteLine("PASS real dependency checks + shared decoder path save");
            using(var form=new PathSettingsForm(settings,"",0)) {
                form.Show();Application.DoEvents();
                var path=form.Controls.Find("FfmpegPath",true);
                Assert(path.Length==1,"FFmpeg field missing");
                Assert(path[0].PointToScreen(System.Drawing.Point.Empty).Y>form.Controls.Find("PythonPath",true)[0].PointToScreen(System.Drawing.Point.Empty).Y,"FFmpeg is not below Python");
                Assert(form.Controls.Find("DetectFfmpeg",true).Length==0&&form.Controls.Find("InstallFfmpeg",true).Length==0,"Old permanent action buttons remain");
                Assert(form.Controls.Find("FindDependencies",true).Length==1&&form.Controls.Find("IdentifyDependencyFolder",true).Length==1,"Global discovery entries missing");
                var save=form.Controls.Find("SavePaths",true)[0];
                var deadline=DateTime.UtcNow.AddSeconds(60);
                while(!save.Enabled&&DateTime.UtcNow<deadline) {Application.DoEvents();System.Threading.Thread.Sleep(50);}
                Assert(save.Enabled,"Ready dependencies did not enable saving: "+form.Controls.Find("FfmpegPathStatus",true)[0].Text);
                Console.WriteLine("PASS asynchronous real probes enable saving");
                using(var bitmap=new System.Drawing.Bitmap(form.Width,form.Height)) {
                    form.DrawToBitmap(bitmap,new System.Drawing.Rectangle(System.Drawing.Point.Empty,form.Size));
                    bitmap.Save(args[1]);
                }
            }
            Console.WriteLine("PASS shared config + field position + detection/install controls");
            Directory.Delete(root,true);
            return 0;
        } catch(Exception e) {Console.WriteLine(e);return 1;}
    }
}
