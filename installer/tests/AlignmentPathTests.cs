using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using AIPraat.Setup;

static class AlignmentPathTests
{
    static int passed,failed;
    static readonly JavaScriptSerializer json=new JavaScriptSerializer();
    static string python,root,wav,exe,acoustic,dictionary;
    const string source="{\"api\":{\"api_key\":\"test\"},\"server\":{\"model_path\":\"keep.gguf\",\"active_preset\":\"keep\"},\"alignment\":{\"backend\":\"mfa\",\"custom\":42,\"mfa\":{\"enabled\":true,\"executable\":\"mfa\",\"conda_executable\":\"keep-conda\",\"conda_environment\":\"keep-env\",\"acoustic_model\":\"english_us_arpa\",\"beam\":20},\"wav2vec2\":{\"enabled\":false,\"model\":\"org/model\",\"device\":\"cpu\",\"token_map\":{\"a\":\"b\"}}}}";
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Case(string name,Action action){try{action();passed++;Console.WriteLine("PASS "+name);}catch(Exception e){failed++;Console.WriteLine("FAIL "+name+": "+(e.InnerException??e).Message);}}
    static Type PathsType(){var type=typeof(Configuration).Assembly.GetType("AIPraat.Setup.AlignmentPaths");Assert(type!=null,"对齐路径配置尚未实现");return type;}
    static object Paths(string w,string e,string a,string d){var p=Activator.CreateInstance(PathsType());foreach(var item in new[]{new[]{"Wav2Vec2ModelPath",w},new[]{"MfaExecutablePath",e},new[]{"MfaAcousticModelPath",a},new[]{"MfaDictionaryPath",d}})PathsType().GetProperty(item[0]).SetValue(p,item[1],null);return p;}
    static string Value(object p,string name){return (string)PathsType().GetProperty(name).GetValue(p,null);}
    static void RequestPaths(InstallRequest request,object p){var property=typeof(InstallRequest).GetProperty("AlignmentPaths");Assert(property!=null,"安装请求缺少对齐路径");property.SetValue(request,p,null);}
    static object Read(Dictionary<string,object> config){return PathsType().GetMethod("Read").Invoke(null,new object[]{config});}
    static void AssertParameters(Dictionary<string,object> config){var a=Configuration.Object(config,"alignment");Assert((string)a["backend"]=="mfa"&&(int)a["custom"]==42,"后端或其他设置改变");var m=Configuration.Object(a,"mfa");var w=Configuration.Object(a,"wav2vec2");Assert((bool)m["enabled"]&&!(bool)w["enabled"]&&(int)m["beam"]==20&&(string)w["device"]=="cpu","启用状态或参数被修改");Assert((string)m["conda_executable"]=="keep-conda"&&(string)m["conda_environment"]=="keep-env"&&Configuration.Object(w,"token_map").Count==1,"已有 Conda/音素配置丢失");}
    static PathSettings Settings(string name){string folder=Path.Combine(root,name);Directory.CreateDirectory(Path.Combine(folder,"ai"));Directory.CreateDirectory(Path.Combine(folder,"profile"));File.WriteAllText(Path.Combine(folder,"ai/ai_config.json"),source);return new PathSettings(Path.Combine(folder,"ai"),Path.Combine(folder,"ai/ai_config.json"),Path.Combine(folder,"profile"),python);}
    static void Save(PathSettings settings,object paths,string result){var method=typeof(PathSettings).GetMethod("SavePaths",new[]{typeof(string),typeof(string),typeof(string),typeof(string),typeof(AlignmentPaths)});Assert(method!=null,"保存尚未包含对齐路径");method.Invoke(settings,new object[]{python,"","",result,paths});}
    static void CheckFields(Form form,string[] names){Assert(!Descendants(form).OfType<TabControl>().Any(),"添加了标签页");foreach(string name in names){var field=form.Controls.Find(name,true);Assert(field.Length==1&&field[0] is TextBox&&field[0].Parent is DependencyRow,"缺少共享路径输入行 "+name);var browse=form.Controls.Find("Browse"+name,true);Assert(browse.Length==1&&browse[0] is OutlineButton&&browse[0].Text==(name=="Wav2Vec2ModelPath"?"选择目录…":"浏览…"),"浏览按钮样式不一致");}}
    static IEnumerable<Control> Descendants(Control parent){foreach(Control c in parent.Controls){yield return c;foreach(var child in Descendants(c))yield return child;}}
    [STAThread]static int Main(string[] args){python=args[0];root=Path.Combine(Path.GetTempPath(),"AIPraat alignment 中文 "+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);wav=Path.Combine(root,"wav 模型");Directory.CreateDirectory(wav);exe=Path.Combine(root,"mfa.exe");acoustic=Path.Combine(root,"声学.zip");dictionary=Path.Combine(root,"发音.dict");foreach(var file in new[]{exe,acoustic,dictionary})File.WriteAllText(file,"fixture");Application.EnableVisualStyles();
        Case("both_hosts_share_collapsed_resource_fields_and_leave_MFA_resources_to_manager",()=>{
            using(var form=new PathSettingsForm(Settings("ui"),"",0)){form.Show();Application.DoEvents();CheckFields(form,new[]{"PythonPath","LlamaServerPath","MmprojPath","MfaExecutablePath"});Assert(form.Controls.Find("Wav2Vec2ModelPath",true).Length==0&&form.Controls.Find("MfaAcousticModelPath",true).Length==0&&form.Controls.Find("MfaDictionaryPath",true).Length==0,"折叠状态或资源管理职责错误");Assert(form.Controls.Find("ModelPath",true).Length==0,"路径配置出现语言模型行");form.Close();}
            using(var form=new WizardForm(new InstallRequest{InstallDirectory=Path.Combine(root,"install")},()=>null)){form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();CheckFields(form,new[]{"PythonPath","LlamaServerPath","ModelPath","MmprojPath","Wav2Vec2ModelPath","MfaExecutablePath"});Assert(!form.Controls.Find("Wav2Vec2ModelPath",true)[0].Visible,"安装器模型资源未折叠");form.Close();}
        });
        Case("read_backfills_existing_model_ids_and_mfa_paths",()=>{var p=Read(json.Deserialize<Dictionary<string,object>>(source));Assert(Value(p,"Wav2Vec2ModelPath")=="org/model"&&Value(p,"MfaExecutablePath")=="mfa"&&Value(p,"MfaAcousticModelPath")=="english_us_arpa"&&Value(p,"MfaDictionaryPath")=="","回填有误");});
        Case("hidden_MFA_resources_preserve_changes_from_manager_when_path_form_saves",()=>{
            var settings=Settings("hidden-resources");AlignmentPaths paths;
            using(var form=new PathSettingsForm(settings,"",0)){paths=((DependencyPanel)form.Controls.Find("DependencyPanel",true)[0]).Alignment;}
            string path=Path.Combine(root,"hidden-resources","ai","ai_config.json");var latest=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));var mfa=Configuration.Object(Configuration.Object(latest,"alignment"),"mfa");
            mfa["acoustic_model"]="manager-model";mfa["acoustic_models"]=new[]{"manager-model"};mfa["dictionary_path"]=dictionary;mfa["dictionary_paths"]=new[]{dictionary};File.WriteAllText(path,json.Serialize(latest));
            settings.SavePaths(python,"","","",paths);
            var saved=Configuration.Object(Configuration.Object(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)),"alignment"),"mfa");
            Assert((string)saved["acoustic_model"]=="manager-model"&&(string)saved["dictionary_path"]==dictionary,"路径保存覆盖了管理窗口选择");
            Assert(saved.ContainsKey("acoustic_models")&&saved.ContainsKey("dictionary_paths"),"资源列表被清除");
        });
        Case("optional_paths_validate_local_files_keep_existing_named_models_and_allow_empty",()=>{
            var valid=Paths(wav,exe,acoustic,dictionary);var method=PathsType().GetMethod("ValidationError");Assert(method.Invoke(valid,null)==null,"已有本地路径被拒绝");Assert(method.Invoke(Paths("","","",""),null)==null,"可选空路径被拒绝");Assert(method.Invoke(Paths("org/model","mfa","english_us_arpa",""),null)==null,"现有模型名称被拒绝");Assert(method.Invoke(Paths(Path.Combine(root,"missing"),"","",""),null)!=null,"无效模型目录被接受");Assert(method.Invoke(Paths("",exe,acoustic,Path.Combine(root,"missing.dict")),null)!=null,"无效词典被接受");
        });
        Case("merge_changes_only_paths_and_preserves_enable_backend_and_parameters",()=>{var config=json.Deserialize<Dictionary<string,object>>(source);var p=Paths(wav,exe,acoustic,dictionary);PathsType().GetMethod("Apply").Invoke(p,new object[]{config,null});AssertParameters(config);var read=Read(config);Assert(Value(read,"Wav2Vec2ModelPath")==wav&&Value(read,"MfaExecutablePath")==exe&&Value(read,"MfaAcousticModelPath")==acoustic&&Value(read,"MfaDictionaryPath")==dictionary,"路径未写入");});
        Case("path_save_round_trips_alignment_and_keeps_api_parameters_with_backup",()=>{var settings=Settings("save");Save(settings,Paths(wav,exe,acoustic,dictionary),"");string folder=Path.Combine(root,"save");var config=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"ai/ai_config.json")));AssertParameters(config);Assert((string)Configuration.Object(config,"api")["api_key"]=="test","API 被修改");var loaded=new PathSettings(Path.Combine(folder,"ai"),Path.Combine(folder,"ai/ai_config.json"),Path.Combine(folder,"profile"),python);var property=typeof(PathSettings).GetProperty("AlignmentPaths");Assert(property!=null&&Value(property.GetValue(loaded,null),"MfaDictionaryPath")==dictionary,"保存后没有回填");Assert(Directory.Exists(Path.Combine(folder,".aipraat-backups")),"保存未备份");});
        Case("unchanged_alignment_fields_preserve_other_window_edits",()=>{var settings=Settings("concurrent");var input=Read(json.Deserialize<Dictionary<string,object>>(source));PathsType().GetProperty("MfaDictionaryPath").SetValue(input,dictionary,null);string file=Path.Combine(root,"concurrent/ai/ai_config.json");var latest=json.Deserialize<Dictionary<string,object>>(source);Configuration.Object(Configuration.Object(latest,"alignment"),"wav2vec2")["model"]="new/model";File.WriteAllText(file,json.Serialize(latest));Save(settings,input,"");var read=Read(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file)));Assert(Value(read,"Wav2Vec2ModelPath")=="new/model"&&Value(read,"MfaDictionaryPath")==dictionary,"未编辑路径覆盖了其他窗口更新");});
        Case("installer_saves_paths_but_skip_preserves_existing_settings",()=>{var request=new InstallRequest();RequestPaths(request,Paths(wav,exe,acoustic,dictionary));var config=Configuration.MakeConfig(source,request,true);AssertParameters(config);Assert(Value(Read(config),"Wav2Vec2ModelPath")==wav&&Value(Read(config),"MfaAcousticModelPath")==acoustic,"安装器没有保存路径");Assert(Value(Read(Configuration.MakeConfig(source,request,false)),"Wav2Vec2ModelPath")=="org/model","跳过配置改变了路径");});
        Case("existing_mfa_path_command_or_conda_ignored_stale_path_keeps_other_paths_editable",()=>{
            foreach(var command in new[]{"mfa.exe",Path.Combine(root,"old-missing-mfa.exe")}) {
                string folder=Path.Combine(root,"legacy-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(Path.Combine(folder,"ai"));Directory.CreateDirectory(Path.Combine(folder,"profile"));var config=json.Deserialize<Dictionary<string,object>>(source);Configuration.Object(Configuration.Object(config,"alignment"),"mfa")["executable"]=command;File.WriteAllText(Path.Combine(folder,"ai/ai_config.json"),json.Serialize(config));
                var settings=new PathSettings(Path.Combine(folder,"ai"),Path.Combine(folder,"ai/ai_config.json"),Path.Combine(folder,"profile"),python);var input=Read(config);PathsType().GetProperty("Wav2Vec2ModelPath").SetValue(input,wav,null);Save(settings,input,"");var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"ai/ai_config.json")));Assert(Value(Read(saved),"MfaExecutablePath")==command&&Value(Read(saved),"Wav2Vec2ModelPath")==wav,"旧 MFA 路径被改变");
                using(var form=new WizardForm(new InstallRequest{InstallDirectory=folder},()=>null)){form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();form.Controls.Find("Wav2Vec2ModelPath",true)[0].Text=wav;var request=(InstallRequest)typeof(WizardForm).GetMethod("Gather",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(form,null);request.ModelPath="";Assert(PathValidation.OptionalPathsError(request)==null,"安装器拒绝未编辑的旧 MFA 路径");form.Close();}
            }
        });
        Case("real_install_engine_persists_alignment_paths_and_skip_reinstall_keeps_bytes",()=>{
            string folder=Path.Combine(root,"engine");Directory.CreateDirectory(Path.Combine(folder,"ai"));File.WriteAllText(Path.Combine(folder,"ai/ai_config.json"),source);
            var request=new InstallRequest {InstallDirectory=folder,ConfigurePrerequisites=true,PythonPath=python,ProfileDirectory=Path.Combine(root,"engine-profile"),ProgramsDirectory=Path.Combine(root,"engine-programs"),DesktopDirectory=Path.Combine(root,"engine-desktop"),CreateDesktopShortcut=false};RequestPaths(request,Paths(wav,exe,acoustic,dictionary));
            Func<MemoryStream> payload=()=>{var stream=new MemoryStream();using(var archive=new System.IO.Compression.ZipArchive(stream,System.IO.Compression.ZipArchiveMode.Create,true)){foreach(string name in new[]{"Praat.exe","AIPraat.exe"})using(var writer=new StreamWriter(archive.CreateEntry(name).Open()))writer.Write("fixture");}stream.Position=0;return stream;};
            using(var stream=payload())new InstallEngine().Run(request,stream,null);var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"ai/ai_config.json")));AssertParameters(saved);Assert(Value(Read(saved),"MfaDictionaryPath")==dictionary&&Value(Read(saved),"Wav2Vec2ModelPath")==wav,"实际安装没有保存路径");
            string before=File.ReadAllText(Path.Combine(folder,"ai/ai_config.json"));request.ConfigurePrerequisites=false;bool blocked=false;using(var stream=payload())try{new InstallEngine().Run(request,stream,null);}catch(IOException){blocked=true;}Assert(blocked&&File.ReadAllText(Path.Combine(folder,"ai/ai_config.json"))==before,"跳过配置未阻止安装");
        });
        Case("clearing_paths_keeps_flags_and_changed_invalid_path_rejects_without_writing",()=>{
            var settings=Settings("clear");Save(settings,Paths("","","",""),"");string file=Path.Combine(root,"clear/ai/ai_config.json");var saved=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file));AssertParameters(saved);Assert(Value(Read(saved),"MfaExecutablePath")==""&&Value(Read(saved),"Wav2Vec2ModelPath")=="","路径未清空");
            string before=File.ReadAllText(file);bool rejected=false;try{Save(settings,Paths("",Path.Combine(root,"new-missing.exe"),"",""),"");}catch(TargetInvocationException e){rejected=e.InnerException is IOException;}Assert(rejected&&File.ReadAllText(file)==before,"无效新路径改写了文件");
        });
        Case("installer_backfills_and_gathers_paths_for_resume",()=>{
            string folder=Path.Combine(root,"wizard");Directory.CreateDirectory(Path.Combine(folder,"ai"));File.WriteAllText(Path.Combine(folder,"ai/ai_config.json"),source);
            using(var form=new WizardForm(new InstallRequest{InstallDirectory=folder},()=>null)) {
                form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();var field=form.Controls.Find("Wav2Vec2ModelPath",true);
                Assert(field.Length==1&&field[0].Text=="org/model","安装器未回填");field[0].Text=wav;form.Controls.Find("MfaExecutablePath",true)[0].Text=exe;
                var request=(InstallRequest)typeof(WizardForm).GetMethod("Gather",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(form,null);
                Assert(request.AlignmentPaths.Wav2Vec2ModelPath==wav&&request.AlignmentPaths.MfaAcousticModelPath=="english_us_arpa"&&request.AlignmentPaths.MfaDictionaryPath=="","请求未保留已管理资源");
                using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"installer-top.png"));}
                var link=form.Controls.Find("ModelResources",true)[0];typeof(LinkLabel).GetMethod("OnLinkClicked",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(link,new object[]{new LinkLabelLinkClickedEventArgs(((LinkLabel)link).Links[0])});
                var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];panel.ScrollControlIntoView(field[0].Parent.Parent);Application.DoEvents();
                using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"installer-bottom.png"));}form.Close();
            }
        });
        Console.WriteLine("RESULT "+passed+" passed, "+failed+" failed; artifacts="+root);return failed==0?0:1;
    }
}
