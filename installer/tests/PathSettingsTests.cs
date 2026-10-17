using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using AIPraat.Setup;

public static class PathSettingsTests
{
    static int passed, failed;
    static readonly JavaScriptSerializer json = new JavaScriptSerializer();
    static readonly string fixture = "{\"api\":{\"enabled\":true,\"locked\":true,\"api_key\":\"test-secret\"},\"qwen\":{\"model\":\"keep-name\",\"max_context_tokens\":12345},\"custom\":42,\"server\":{\"llama_server\":\"old.exe\",\"model_path\":\"D:/模型/model.gguf\",\"mmproj_path\":\"old.gguf\",\"active_preset\":\"chosen\",\"presets\":[{\"id\":\"chosen\",\"model_path\":\"D:/模型/model.gguf\",\"mmproj_path\":\"old.gguf\",\"vision\":true,\"context_tokens\":6000},{\"id\":\"other\",\"model_path\":\"D:/other.gguf\",\"mmproj_path\":\"other-mm.gguf\",\"vision\":true}],\"mmproj_by_model\":{\"D:/模型/model.gguf\":\"old.gguf\",\"D:/other.gguf\":\"other-mm.gguf\"}}}";
    static void Assert(bool ok, string detail) { if (!ok) throw new Exception(detail); }
    static void Case(string name, Action action)
    {
        try { action(); passed++; Console.WriteLine("PASS " + name); }
        catch (Exception e) { failed++; Console.WriteLine("FAIL " + name + ": " + (e.InnerException ?? e).Message); }
    }
    static Type Production(string name)
    {
        var type = typeof(Configuration).Assembly.GetType("AIPraat.Setup." + name);
        Assert(type != null, "路径配置功能尚未实现：" + name);
        return type;
    }
    static Dictionary<string,object> Merge(string source, string llama, string mmproj)
    {
        return (Dictionary<string,object>)Production("PathSettings").GetMethod("MergeConfig").Invoke(null, new object[] { source, llama, mmproj });
    }
    static object Settings(string root, string currentPython)
    {
        return Activator.CreateInstance(Production("PathSettings"), new object[] {Path.Combine(root,"ai"), Path.Combine(root,"ai","ai_config.json"), Path.Combine(root,"profile"), currentPython});
    }
    static void Save(object settings, string python, string llama, string mmproj, string result)
    {
        Production("PathSettings").GetMethod("Save").Invoke(settings,new object[]{python,llama,mmproj,result});
    }
    static string Property(object settings, string name) { return (string)Production("PathSettings").GetProperty(name).GetValue(settings,null); }
    static string Fixture(string root, string name)
    {
        string folder=Path.Combine(root,name); Directory.CreateDirectory(Path.Combine(folder,"ai")); Directory.CreateDirectory(Path.Combine(folder,"profile"));
        File.WriteAllText(Path.Combine(folder,"ai","ai_config.json"),fixture);
        File.WriteAllText(Path.Combine(folder,"install-settings.json"),"{\"python_path\":\"old-python\",\"version\":\"keep\"}");
        File.WriteAllText(Path.Combine(folder,"profile","Preferences.txt"),"Other.setting: keep\nPython.executablePath: old-python\n");
        return folder;
    }
    [STAThread]
    public static int Main(string[] args)
    {
        string root=Path.Combine(Path.GetTempPath(),"AIPraat-path-tests-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
        Case("path_edit_keeps_model_api_and_unknown_settings",()=>{
            var c=Merge(fixture,"D:/new/llama-server.exe","D:/new/mm.gguf"); var s=Configuration.Object(c,"server");
            Assert((string)s["model_path"]=="D:/模型/model.gguf" && (string)s["active_preset"]=="chosen","模型或预设被重置");
            Assert((string)Configuration.Object(c,"api")["api_key"]=="test-secret" && (bool)Configuration.Object(c,"api")["locked"],"API 设置丢失");
            Assert((int)c["custom"]==42 && (int)Configuration.Object(c,"qwen")["max_context_tokens"]==12345,"无关设置丢失");
            Assert((string)s["llama_server"]=="D:/new/llama-server.exe" && (string)s["mmproj_path"]=="D:/new/mm.gguf","新路径未保存");
        });
        Case("projection_updates_current_model_binding_and_preset_only",()=>{
            var s=Configuration.Object(Merge(fixture,"","D:/new/mm.gguf"),"server"); var presets=((System.Collections.IEnumerable)s["presets"]).Cast<Dictionary<string,object>>().ToArray();
            Assert((string)Configuration.Object(s,"mmproj_by_model")["D:/模型/model.gguf"]=="D:/new/mm.gguf","模型投影关联未更新");
            Assert((string)presets[0]["mmproj_path"]=="D:/new/mm.gguf" && (bool)presets[0]["vision"],"当前预设投影未更新");
            Assert((string)presets[1]["mmproj_path"]=="other-mm.gguf","其他模型投影被覆盖");
        });
        Case("clearing_projection_removes_binding_and_disables_preset_vision",()=>{
            var s=Configuration.Object(Merge(fixture,"",""),"server"); var presets=((System.Collections.IEnumerable)s["presets"]).Cast<Dictionary<string,object>>().ToArray();
            Assert((string)s["mmproj_path"]=="" && !Configuration.Object(s,"mmproj_by_model").ContainsKey("D:/模型/model.gguf"),"旧投影残留");
            Assert(!(bool)presets[0]["vision"] && (string)presets[0]["mmproj_path"]=="","预设仍启用旧投影");
            Assert((string)Configuration.Object(s,"mmproj_by_model")["D:/other.gguf"]=="other-mm.gguf","其他模型关联丢失");
        });
        Case("no_model_configured_does_not_invent_model_or_preset",()=>{
            var s=Configuration.Object(Merge(Configuration.DefaultJson,"",""),"server");
            Assert((string)s["model_path"]=="" && (string)s["active_preset"]=="" && !((bool)s["auto_start"]),"空模型被覆盖或自动启动");
        });
        Case("unchanged_optional_paths_preserve_auto_start_and_vision_choices",()=>{
            var source=json.Deserialize<Dictionary<string,object>>(fixture);var server=Configuration.Object(source,"server");server["auto_start"]=false;
            Configuration.Object(source,"qwen")["vision_when_requested"]=false;
            ((System.Collections.IEnumerable)server["presets"]).Cast<Dictionary<string,object>>().First()["vision"]=false;
            var saved=Merge(json.Serialize(source),"old.exe","old.gguf");var s=Configuration.Object(saved,"server");
            Assert(!(bool)s["auto_start"] && !(bool)Configuration.Object(saved,"qwen")["vision_when_requested"],"仅修改 Python 改变了启动或视觉选项");
            Assert(!(bool)((System.Collections.IEnumerable)s["presets"]).Cast<Dictionary<string,object>>().First()["vision"],"现有预设的视觉选项改变");
        });
        Case("projection_edit_preserves_another_preset_for_same_model",()=>{
            var source=json.Deserialize<Dictionary<string,object>>(fixture);var presets=((System.Collections.IEnumerable)Configuration.Object(source,"server")["presets"]).Cast<Dictionary<string,object>>().ToArray();
            presets[1]["model_path"]="D:/模型/model.gguf";presets[1]["vision"]=false;
            var s=Configuration.Object(Merge(json.Serialize(source),"old.exe","new-mm.gguf"),"server");
            var saved=((System.Collections.IEnumerable)s["presets"]).Cast<Dictionary<string,object>>().ToArray();
            Assert((string)saved[0]["mmproj_path"]=="new-mm.gguf","当前预设未更新");
            Assert((string)saved[1]["mmproj_path"]=="other-mm.gguf" && !(bool)saved[1]["vision"],"同模型的另一个预设被覆盖");
        });
        Case("active_preset_id_lookup_matches_python_case_insensitive_behavior",()=>{
            var source=json.Deserialize<Dictionary<string,object>>(fixture);var s=Configuration.Object(source,"server");var presets=((System.Collections.IEnumerable)s["presets"]).Cast<Dictionary<string,object>>().ToArray();
            presets[1]["model_path"]="D:/模型/model.gguf";s["presets"]=new[]{presets[1],presets[0]};s["active_preset"]="CHOSEN";
            var result=Configuration.Object(Merge(json.Serialize(source),"old.exe","new-mm.gguf"),"server");
            var saved=((System.Collections.IEnumerable)result["presets"]).Cast<Dictionary<string,object>>().ToArray();
            Assert((string)saved[0]["mmproj_path"]=="other-mm.gguf" && (string)saved[1]["mmproj_path"]=="new-mm.gguf","未找到大小写不同的活动预设");
        });
        Case("load_prefers_running_python_and_reads_optional_paths",()=>{
            string folder=Fixture(root,"load"); var settings=Settings(folder,"running-python");
            Assert(Property(settings,"PythonPath")=="running-python","当前 Python 未回填");
            Assert(Property(settings,"LlamaServerPath")=="old.exe" && Property(settings,"MmprojPath")=="old.gguf","路径未回填");
            Assert(Property(Settings(folder,""),"PythonPath")=="old-python","安装路径未回填");
        });
        Case("dialog_opens_without_python_and_cancel_leaves_files_unchanged",()=>{
            string folder=Fixture(root,"dialog"); var settings=Settings(folder,"missing-python"); string before=File.ReadAllText(Path.Combine(folder,"ai","ai_config.json"));
            using(var form=(Form)Activator.CreateInstance(Production("PathSettingsForm"),new object[]{settings,Path.Combine(folder,"result.txt"),0})) {
                form.Show(); Application.DoEvents();
                Assert(form.Controls.Find("PythonPath",true).Length==1 && form.Controls.Find("LlamaServerPath",true).Length==1 && form.Controls.Find("MmprojPath",true).Length==1,"缺少三个配置字段");
                Assert(form.Controls.Find("ModelPath",true).Length==0,"路径配置仍出现语言模型行");
                Assert(form.Controls.Find("Wav2Vec2ModelPath",true).Length==0,"路径配置仍出现 wav2vec2 模型行");
                Assert(form.Controls.Find("MfaExecutablePath",true).Length==1,"MFA 程序路径丢失");
                Assert(!form.Controls.Find("SavePaths",true)[0].Enabled,"无效 Python 可以保存");
                using(var bitmap=new System.Drawing.Bitmap(form.Width,form.Height)) {
                    form.DrawToBitmap(bitmap,new System.Drawing.Rectangle(System.Drawing.Point.Empty,form.Size));
                    bitmap.Save(Path.Combine(folder,"path-settings.png"),System.Drawing.Imaging.ImageFormat.Png);
                }
                ((Button)form.Controls.Find("CancelPaths",true)[0]).PerformClick(); Application.DoEvents();
            }
            Assert(File.ReadAllText(Path.Combine(folder,"ai","ai_config.json"))==before && !File.Exists(Path.Combine(folder,"result.txt")),"取消更改了配置");
        });
        if(args.Length>0) {
            string python=args[0];
            Case("save_syncs_installation_preferences_result_and_config",()=>{
                string folder=Fixture(root,"save 中文 空格"); string llama=Path.Combine(folder,"llama-server.exe"), mm=Path.Combine(folder,"视觉.gguf"), result=Path.Combine(folder,"result.txt");
                File.WriteAllText(llama,"fixture");File.WriteAllText(mm,"fixture");
                Save(Settings(folder,python),python,llama,mm,result);
                Assert(File.ReadAllText(result)==python,"Praat 返回路径错误");
                var install=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"install-settings.json")));
                Assert((string)install["python_path"]==python && (string)install["version"]=="keep","安装信息未保留");
                string prefs=File.ReadAllText(Path.Combine(folder,"profile","Preferences.txt")); Assert(prefs.Contains("Other.setting: keep")&&prefs.Contains("Python.executablePath: "+python),"首选项未同步");
                var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"ai","ai_config.json")));
                Assert((string)Configuration.Object(saved,"server")["mmproj_path"]==mm && (string)Configuration.Object(saved,"server")["model_path"]=="D:/模型/model.gguf","模型路径保存错误");
                Assert(Directory.GetFiles(Path.Combine(folder,".aipraat-backups"),"*",SearchOption.AllDirectories).Any(x=>File.ReadAllText(x)==fixture),"旧配置无备份");
            });
            Case("locked_preferences_roll_back_config_and_install_settings",()=>{
                string folder=Fixture(root,"locked");string config=Path.Combine(folder,"ai","ai_config.json"),install=Path.Combine(folder,"install-settings.json"),pref=Path.Combine(folder,"profile","Preferences.txt");string oldInstall=File.ReadAllText(install);var settings=Settings(folder,python);bool rejected=false;
                using(var locked=new FileStream(pref,FileMode.Open,FileAccess.Read,FileShare.Read)) {
                    try {Save(settings,python,"","",Path.Combine(folder,"result.txt"));} catch(TargetInvocationException e) {rejected=e.InnerException is IOException;}
                }
                Assert(rejected,"锁定文件未阻止保存"); Assert(File.ReadAllText(config)==fixture && File.ReadAllText(install)==oldInstall && !File.Exists(Path.Combine(folder,"result.txt")),"保存失败留下部分修改");
            });
            Case("invalid_paths_do_not_write_any_files",()=>{
                string folder=Fixture(root,"invalid"); bool rejected=false;
                try {Save(Settings(folder,python),python,Path.Combine(folder,"missing.exe"),"",Path.Combine(folder,"result.txt"));} catch(TargetInvocationException e) {rejected=e.InnerException is IOException;}
                Assert(rejected && File.ReadAllText(Path.Combine(folder,"ai","ai_config.json"))==fixture && !File.Exists(Path.Combine(folder,"result.txt")),"无效路径写入配置");
            });
            Case("concurrent_config_edits_are_merged_from_latest_file",()=>{
                string folder=Fixture(root,"latest");var settings=Settings(folder,python);string path=Path.Combine(folder,"ai","ai_config.json");File.WriteAllText(path,fixture.Replace("test-secret","new-secret"));
                Save(settings,python,"","",Path.Combine(folder,"result.txt"));
                Assert((string)Configuration.Object(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)),"api")["api_key"]=="new-secret","保存覆盖了打开窗口后更新的 API");
            });
            Case("model_switch_while_dialog_open_rejects_stale_projection",()=>{
                string folder=Fixture(root,"model-changed"),path=Path.Combine(folder,"ai","ai_config.json"),oldProjection=Path.Combine(folder,"old.gguf");File.WriteAllText(oldProjection,"fixture");
                var source=json.Deserialize<Dictionary<string,object>>(fixture);Configuration.Object(source,"server")["mmproj_path"]=oldProjection;File.WriteAllText(path,json.Serialize(source));
                var settings=Settings(folder,python);string latest=json.Serialize(source).Replace("D:/模型/model.gguf","D:/new/model-b.gguf");File.WriteAllText(path,latest);bool rejected=false;
                try {Save(settings,python,"",oldProjection,Path.Combine(folder,"result.txt"));} catch(TargetInvocationException e) {rejected=e.InnerException is IOException;}
                Assert(rejected && File.ReadAllText(path)==latest && !File.Exists(Path.Combine(folder,"result.txt")),"旧窗口覆盖了新模型的投影");
            });
            Case("config_writer_lock_prevents_overlapping_path_write",()=>{
                string folder=Fixture(root,"write-lock");var settings=Settings(folder,python);string path=Path.Combine(folder,"ai","ai_config.json");bool rejected=false;
                using(var locked=new FileStream(path+".lock",FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.ReadWrite)) {
                    locked.WriteByte(0);locked.Flush();locked.Lock(0,1);
                    try {Save(settings,python,"","",Path.Combine(folder,"result.txt"));} catch(TargetInvocationException e) {rejected=e.InnerException is IOException;}
                    finally {locked.Unlock(0,1);}
                }
                Assert(rejected && File.ReadAllText(path)==fixture && !File.Exists(Path.Combine(folder,"result.txt")),"未与 API/模型写入共享锁");
            });
        }
        Console.WriteLine("RESULT "+passed+" passed, "+failed+" failed; artifacts="+root);return failed==0?0:1;
    }
}
