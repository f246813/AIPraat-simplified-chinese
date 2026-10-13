using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Collections.Generic;
using System.Text;
using System.Reflection;
using System.Windows.Forms;
using System.Drawing;
using AIPraat.Setup;

public static class ContractTests
{
    static string readyPython;
    static int passed, failed;
    static void Assert(bool ok, string detail) { if (!ok) throw new Exception(detail); }
    static void Case(string name, Action test)
    {
        try { test(); passed++; Console.WriteLine("PASS " + name); }
        catch (Exception e) { failed++; Console.WriteLine("FAIL " + name + ": " + e.Message); }
    }
    static InstallRequest Request(string root)
    {
        return new InstallRequest { InstallDirectory = Path.Combine(root, "AIPraat 中文 目录"),
            ProfileDirectory = Path.Combine(root, "profile"), ProgramsDirectory = Path.Combine(root, "programs"),
            DesktopDirectory = Path.Combine(root, "desktop"), CreateDesktopShortcut = false,
            ConfigurePrerequisites=true, PythonPath=readyPython, RuntimeDevice="cpu" };
    }
    static MemoryStream Payload(Dictionary<string,string> files)
    {
        var result = new MemoryStream();
        using (var zip = new ZipArchive(result, ZipArchiveMode.Create, true))
            foreach (var pair in files)
                using (var w = new StreamWriter(zip.CreateEntry(pair.Key).Open(), new UTF8Encoding(false))) w.Write(pair.Value);
        result.Position = 0; return result;
    }
    static Dictionary<string,string> Files()
    {
        return new Dictionary<string,string> { {"Praat.exe", "new-praat"}, {"AIPraat.exe", "launcher"},
            {"ai/ai_config.example.json", Configuration.DefaultJson},
            {"ai/plugin/praat_ai/setup.praat", "# setup"},
            {"ai/plugin/praat_ai/praatAiMeasure.praat", "# measure"},
            {"ai/plugin/praat_ai/praatAiMeasureEditor.praat", "# editor"} };
    }
    [STAThread]
    public static int Main(string[] args)
    {
        readyPython=args[0];
        string root = Path.Combine(Path.GetTempPath(), "AIPraat-contract-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        Case("default_exact_C_drive_folder", () => Assert(PathValidation.DefaultDirectory == @"C:\AIPraat", "wrong default"));
        Case("reject_drive_root_and_relative", () => {
            Assert(PathValidation.InstallDirectoryError(@"C:\") != null, "drive root accepted");
            Assert(PathValidation.InstallDirectoryError("relative") != null, "relative accepted");
            Assert(PathValidation.InstallDirectoryError(Path.Combine(root,"正常 路径")) == null, "unicode rejected");
        });
        Case("optional_empty_paths_are_valid", () => Assert(PathValidation.OptionalPathsError(Request(root)) == null,"empty optional rejected"));
        Case("optional_missing_model_is_invalid", () => {
            var r=Request(root); r.ModelPath=Path.Combine(root,"missing.gguf");
            Assert(PathValidation.OptionalPathsError(r) != null,"missing model accepted");
        });
        Case("optional_wrong_extension_is_invalid", () => {
            var r=Request(root); r.ModelPath=Path.Combine(root,"wrong.txt"); File.WriteAllText(r.ModelPath,"x");
            Assert(PathValidation.OptionalPathsError(r) != null,"non-GGUF accepted");
        });
        Case("optional_existing_files_are_accepted", () => {
            var r=Request(root); r.ModelPath=Path.Combine(root,"模型.gguf");r.MmprojPath=Path.Combine(root,"视觉.gguf");r.LlamaServerPath=Path.Combine(root,"llama-server.exe");
            foreach(string file in new[]{r.ModelPath,r.MmprojPath,r.LlamaServerPath}) File.WriteAllText(file,"fixture");
            Assert(PathValidation.OptionalPathsError(r)==null,"existing optional files rejected");
        });
        Case("default_config_has_no_developer_paths_or_credentials", () => {
            var c=Configuration.MakeConfig(Configuration.DefaultJson, Request(root), false);
            var s=Configuration.Object(c,"server");
            Assert((string)s["llama_server"] == "" && (string)s["model_path"] == "", "development paths");
            Assert((string)Configuration.Object(c,"api")["api_key"] == "", "credential");
            Assert(!(bool)s["auto_start"], "empty paths start automatically");
        });
        Case("configuration_keeps_user_api_and_selected_optional_model", () => {
            var r=Request(root); r.ConfigurePrerequisites=true; r.ModelPath=@"D:\模型 文件\model.gguf";
            var c=Configuration.MakeConfig("{\"api\":{\"api_key\":\"user-secret\",\"enabled\":true},\"other\":123}",r,true);
            Assert((string)Configuration.Object(c,"api")["api_key"]=="user-secret","user secret lost");
            Assert((int)c["other"]==123,"unknown user key lost");
            Assert((string)Configuration.Object(c,"server")["model_path"]==r.ModelPath,"model lost");
            Assert(!(bool)Configuration.Object(c,"server")["auto_start"],"model without server auto-started");
        });
        Case("reconfigure_preserves_other_presets_and_model_bindings", () => {
            var r=Request(root); r.ModelPath=@"D:\models\selected.gguf"; r.MmprojPath=@"D:\models\new-vision.gguf";
            string existing="{\"server\":{\"presets\":[{\"id\":\"custom\",\"model_path\":\"D:\\\\models\\\\selected.gguf\",\"context_tokens\":32768,\"custom_setting\":123},{\"id\":\"other\",\"model_path\":\"other.gguf\"}],\"mmproj_by_model\":{\"other.gguf\":\"other-vision.gguf\"}}}";
            var server=Configuration.Object(Configuration.MakeConfig(existing,r,true),"server");
            var presets=((System.Collections.IEnumerable)server["presets"]).Cast<Dictionary<string,object>>().ToList();
            Assert(presets.Count==2 && (int)presets.Single(x=>(string)x["id"]=="custom")["context_tokens"]==32768,"custom presets/options lost");
            Assert((int)presets.Single(x=>(string)x["id"]=="custom")["custom_setting"]==123,"custom option lost");
            Assert((string)server["active_preset"]=="custom","selected existing preset not reused");
            Assert((string)Configuration.Object(server,"mmproj_by_model")["other.gguf"]=="other-vision.gguf","other binding lost");
        });
        Case("python_only_reconfigure_keeps_saved_presets", () => {
            var server=Configuration.Object(Configuration.MakeConfig("{\"server\":{\"presets\":[{\"id\":\"saved\",\"model_path\":\"old.gguf\"}],\"mmproj_by_model\":{\"old.gguf\":\"old-vision.gguf\"}}}",Request(root),true),"server");
            Assert(((System.Collections.IEnumerable)server["presets"]).Cast<object>().Count()==1,"saved preset removed");
            Assert(Configuration.Object(server,"mmproj_by_model").ContainsKey("old.gguf"),"saved vision removed");
        });
        Case("resume_restores_all_selections_for_retry", () => {
            var r=Request(root); r.ConfigurePrerequisites=true; r.PythonPath=@"D:\Python\python.exe";
            r.ModelPath=@"D:\模型\model.gguf"; r.MmprojPath=@"D:\模型\vision.gguf"; r.LlamaServerPath=@"D:\llama\llama-server.exe";
            using(var form=new WizardForm(r,()=>Payload(Files()))) {
                form.Resume();
                var gathered=(InstallRequest)typeof(WizardForm).GetMethod("Gather",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(form,null);
                Assert(gathered.ConfigurePrerequisites && gathered.PythonPath==r.PythonPath && gathered.ModelPath==r.ModelPath && gathered.MmprojPath==r.MmprojPath && gathered.LlamaServerPath==r.LlamaServerPath,"resume dropped chosen prerequisites");
            }
        });
        Case("wizard_colors_and_pending_probe_generation", () => {
            var flags=BindingFlags.NonPublic|BindingFlags.Instance;
            using(var form=new WizardForm(Request(root),()=>Payload(Files()))) {
                var next=(OutlineButton)typeof(WizardForm).GetField("next",flags).GetValue(form);
                typeof(WizardForm).GetMethod("ShowStep",flags).Invoke(form,new object[]{1});
                Assert(!next.Enabled&&next.Primary&&next.OutlineColor==Color.FromArgb(204,211,221),"pending primary action not disabled");
                var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];
                var generation=typeof(DependencyPanel).GetField("pythonVersion",flags);
                Assert(generation!=null,"probe generation missing");
                int before=(int)generation.GetValue(panel);form.Controls.Find("PythonPath",true)[0].Text="new-python.exe";
                Assert((int)generation.GetValue(panel)>before,"old probe not invalidated");
                typeof(WizardForm).GetField("installing",flags).SetValue(form,true);
                typeof(WizardForm).GetMethod("ShowStep",flags).Invoke(form,new object[]{2});
                Assert(!next.Enabled&&next.Text=="安装中…","installation presents retry prematurely");
                typeof(WizardForm).GetField("installing",flags).SetValue(form,false);
            }
        });
        Case("resume_accepts_redirected_user_folders_and_rejects_mismatch", () => {
            var r=Request(root);
            r.ProfileDirectory=@"D:\Redirected\AppData\Praat"; r.ProgramsDirectory=@"\\server\share\Programs\AIPraat"; r.DesktopDirectory=@"D:\OneDrive\Desktop";
            r.ValidateUserDestinations(@"D:\Redirected\AppData",@"\\server\share\Programs",@"D:\OneDrive\Desktop");
            r.DesktopDirectory=@"C:\Windows"; bool rejected=false;
            try {r.ValidateUserDestinations(@"D:\Redirected\AppData",@"\\server\share\Programs",@"D:\OneDrive\Desktop");} catch(InvalidDataException) {rejected=true;}
            Assert(rejected,"different user's destination accepted");
        });
        Case("preferences_merge_preserves_unrelated_settings", () => {
            string text=Configuration.MergePreferences("Other.setting: yes\nPython.executablePath: old\nAI.projectDirectory: old\n", @"D:\新 目录\ai", @"D:\Python 环境\python.exe");
            Assert(text.Contains("Other.setting: yes"),"unrelated lost");
            Assert(text.Contains(@"Python.executablePath: D:\Python 环境\python.exe"),"wrong python");
            Assert(text.Split('\n').Count(x=>x.StartsWith("Python.executablePath:"))==1,"duplicate key");
        });
        Case("skip_preserves_python_preference", () => Assert(
            Configuration.MergePreferences("Python.executablePath: keep\n", @"D:\AIPraat\ai", null).Contains("Python.executablePath: keep"),"skip resets Python"));
        Case("install_reuses_existing_directory_preserves_data_and_reaches_100", () => {
            var r=Request(Path.Combine(root,"reuse")); Directory.CreateDirectory(r.InstallDirectory);
            File.WriteAllText(Path.Combine(r.InstallDirectory,"my.wav"),"user-audio");
            File.WriteAllText(Path.Combine(r.InstallDirectory,"Praat.exe"),"old-praat");
            Directory.CreateDirectory(Path.Combine(r.InstallDirectory,"ai"));
            File.WriteAllText(Path.Combine(r.InstallDirectory,"ai","ai_config.json"),"{\"api\":{\"api_key\":\"private\"},\"server\":{\"model_path\":\"keep.gguf\"}}");
            var progress=new List<int>();
            using(var p=Payload(Files())) new InstallEngine().Run(r,p,x=>progress.Add(x.Percent));
            Assert(File.ReadAllText(Path.Combine(r.InstallDirectory,"my.wav"))=="user-audio","user file changed");
            Assert(File.ReadAllText(Path.Combine(r.InstallDirectory,"Praat.exe"))=="new-praat","not installed");
            Assert(!Directory.Exists(Path.Combine(r.InstallDirectory,"AIPraat")),"nested folder created");
            Assert(File.ReadAllText(Path.Combine(r.InstallDirectory,"ai","ai_config.json")).Contains("private"),"user configuration lost");
            Assert(progress.Last()==100 && progress.SequenceEqual(progress.OrderBy(x=>x)), "progress wrong");
            Assert(Directory.EnumerateFiles(Path.Combine(r.InstallDirectory,".aipraat-backups"),"*",SearchOption.AllDirectories)
                .Any(x=>File.ReadAllText(x)=="old-praat"),"backup missing");
        });
        Case("archive_traversal_cannot_escape", () => {
            var r=Request(Path.Combine(root,"traversal"));
            bool rejected=false; using(var p=Payload(new Dictionary<string,string>{{"../outside.txt","bad"}}))
                try { new InstallEngine().Run(r,p,null); } catch(InvalidDataException) { rejected=true; }
            Assert(rejected,"traversal accepted"); Assert(!File.Exists(Path.Combine(root,"outside.txt")),"escaped");
        });
        Case("locked_file_rolls_back_prior_changes", () => {
            var r=Request(Path.Combine(root,"rollback")); Directory.CreateDirectory(r.InstallDirectory);
            File.WriteAllText(Path.Combine(r.InstallDirectory,"Praat.exe"),"original");
            File.WriteAllText(Path.Combine(r.InstallDirectory,"AIPraat.exe"),"locked");
            bool threw=false;
            using(var locked=new FileStream(Path.Combine(r.InstallDirectory,"AIPraat.exe"),FileMode.Open,FileAccess.Read,FileShare.None))
            using(var p=Payload(Files())) try { new InstallEngine().Run(r,p,null); } catch(IOException) { threw=true; }
            Assert(threw,"lock not reported");
            Assert(File.ReadAllText(Path.Combine(r.InstallDirectory,"Praat.exe"))=="original","prior overwrite not rolled back");
        });
        Case("python_missing_path_is_rejected", () => Assert(!PathValidation.ProbePython(Path.Combine(root,"missing.exe")).Valid,"missing Python accepted"));
        if(args.Length>0) Case("real_python_capability_probe",()=> {
            var result=PathValidation.ProbePython(args[0]); Assert(result.Valid,result.Message);
        });
        if(args.Length>1) Case("real_python_without_dependencies_is_rejected",()=> {
            var result=PathValidation.ProbePython(args[1]);
            Assert(!result.Valid && result.Message.Contains("NumPy") && result.Message.Contains("Pillow"),"missing dependency environment accepted or not explained: "+result.Message);
        });
        Console.WriteLine("RESULT "+passed+" passed, "+failed+" failed; artifacts="+root);
        return failed==0?0:1;
    }
}
