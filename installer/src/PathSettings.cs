using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace AIPraat.Setup
{
    public sealed class PathSettings
    {
        readonly string aiDirectory, configPath, profileDirectory, installSettings;
        readonly string openedModel, openedPreset;
        static readonly Encoding utf8=new UTF8Encoding(false);
        public string PythonPath {get;private set;}
        public string FfmpegPath {get;private set;}
        public string LlamaServerPath {get;private set;}
        public string ModelPath {get;private set;}
        public string MmprojPath {get;private set;}
        public AlignmentPaths AlignmentPaths {get;private set;}
        public string RuntimeDevice {get;private set;}
        public PathSettings(string aiDirectory,string configPath,string profileDirectory,string currentPython)
        {
            this.aiDirectory=Path.GetFullPath(aiDirectory); this.configPath=Path.GetFullPath(configPath);
            this.profileDirectory=Path.GetFullPath(profileDirectory);
            installSettings=Path.Combine(Path.GetDirectoryName(this.aiDirectory),"install-settings.json");
            var config=ReadObject(this.configPath,Configuration.DefaultJson); var server=Configuration.Object(config,"server");
            var audio=Configuration.Object(config,"audio");
            FfmpegPath=Value(audio,"ffmpeg");
            string legacy=Path.Combine(this.aiDirectory,"anki_config.json");
            if(!audio.ContainsKey("ffmpeg")&&File.Exists(legacy))FfmpegPath=Value(ReadObject(legacy,"{}"),"ffmpeg");
            openedModel=Value(server,"model_path");openedPreset=Value(server,"active_preset");
            ModelPath=openedModel;
            LlamaServerPath=Value(server,"llama_server"); MmprojPath=Value(server,"mmproj_path");
            AlignmentPaths=AIPraat.Setup.AlignmentPaths.Read(config);
            RuntimeDevice=Value(Configuration.Object(Configuration.Object(config,"alignment"),"wav2vec2"),"device");
            if(RuntimeDevice!="cuda")RuntimeDevice="cpu";
            PythonPath=currentPython??"";
            if(PythonPath=="" || PythonPath=="python" || PythonPath=="python3") {
                string installed=File.Exists(installSettings)?Value(ReadObject(installSettings,"{}"),"python_path"):"";
                string preference=Configuration.ReadPythonPreference(this.profileDirectory);
                PythonPath=installed!=""?installed:preference;
            }
        }
        static string Value(Dictionary<string,object> data,string key)
        {
            object value; return data.TryGetValue(key,out value)?Convert.ToString(value):"";
        }
        static Dictionary<string,object> ReadObject(string path,string fallback)
        {
            string text=File.Exists(path)?File.ReadAllText(path):fallback;
            var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(text);
            if(data==null) throw new InvalidDataException("配置不是 JSON 对象："+path);
            return data;
        }
        public static Dictionary<string,object> MergeConfig(string existing,string llama,string mmproj)
        {
            var config=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(string.IsNullOrWhiteSpace(existing)?Configuration.DefaultJson:existing);
            if(config==null) throw new InvalidDataException("AI 配置不是 JSON 对象，请先修复 ai_config.json。");
            var server=Configuration.Object(config,"server");
            string model=Value(server,"model_path"), oldMmproj=Value(server,"mmproj_path"); llama=PathValidation.Clean(llama); mmproj=PathValidation.Clean(mmproj);
            server["llama_server"]=llama; server["mmproj_path"]=mmproj;
            bool projectionChanged=!SameModel(oldMmproj,mmproj);
            if(model!="" && projectionChanged) {
                var bindings=Configuration.Object(server,"mmproj_by_model");
                foreach(string key in bindings.Keys.Where(x=>SameModel(x,model)).ToArray()) bindings.Remove(key);
                if(mmproj!="") bindings[model]=mmproj;
                object raw;
                if(server.TryGetValue("presets",out raw) && raw is System.Collections.IEnumerable && !(raw is string)) {
                    var presets=((System.Collections.IEnumerable)raw).OfType<Dictionary<string,object>>().Where(x=>SameModel(Value(x,"model_path"),model)).ToArray();
                    var preset=presets.FirstOrDefault(x=>string.Equals(Value(x,"id"),Value(server,"active_preset"),StringComparison.OrdinalIgnoreCase)) ?? presets.FirstOrDefault();
                    if(preset!=null) {preset["mmproj_path"]=mmproj; preset["vision"]=mmproj!="";}
                }
            }
            if(projectionChanged) Configuration.Object(config,"qwen")["vision_when_requested"]=mmproj!="";
            return config;
        }
        static bool SameModel(string left,string right)
        {
            return string.Equals(left.Replace('\\','/'),right.Replace('\\','/'),StringComparison.OrdinalIgnoreCase);
        }
        public void Save(string python,string llama,string mmproj,string resultPath)
        {
            SavePaths(python,llama,mmproj,resultPath,null);
        }
        public void SavePaths(string python,string llama,string mmproj,string resultPath,AlignmentPaths alignmentPaths)
        {
            SavePaths(python,llama,mmproj,resultPath,alignmentPaths,RuntimeDevice);
        }
        public void SavePaths(string python,string llama,string mmproj,string resultPath,AlignmentPaths alignmentPaths,string device)
        {
            SavePaths(python,llama,mmproj,resultPath,alignmentPaths,device,null);
        }
        public void SavePaths(string python,string llama,string mmproj,string resultPath,AlignmentPaths alignmentPaths,string device,string ffmpeg)
        {
            FfmpegProbe audioProbe=null;
            if(ffmpeg!=null) {audioProbe=FfmpegDependencies.Probe(ffmpeg);if(!audioProbe.Ready)throw new IOException(audioProbe.Message);}
            python=PathValidation.Clean(python); llama=PathValidation.Clean(llama); mmproj=PathValidation.Clean(mmproj);
            var probe=RuntimeDependencies.Probe(python,device,true,null); if(!probe.Valid) throw new IOException(probe.Message);
            string error=PathValidation.OptionalPathsError(new InstallRequest {LlamaServerPath=llama,MmprojPath=mmproj,AlignmentPaths=alignmentPaths,OriginalAlignmentPaths=AlignmentPaths});
            if(error!=null) throw new IOException(error);
            Directory.CreateDirectory(Path.Combine(aiDirectory,"runtime"));
            using(var gate=new FileStream(Path.Combine(aiDirectory,"runtime","service_transition.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.ReadWrite)) {
                if(gate.Length==0) {gate.WriteByte(0);gate.Flush();}
                try {gate.Lock(0,1);} catch(IOException) {throw new IOException("前端正在切换或加载模型，请稍后再保存路径。");}
                try {
                  using(var configGate=new FileStream(configPath+".lock",FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.ReadWrite)) {
                    if(configGate.Length==0) {configGate.WriteByte(0);configGate.Flush();}
                    try {configGate.Lock(0,1);} catch(IOException) {throw new IOException("其他窗口正在保存配置，请稍后重试。");}
                    try {
                    // Re-read on save so API/model changes made while the dialog was open survive.
                    string existing=File.Exists(configPath)?File.ReadAllText(configPath):Configuration.DefaultJson;
                    var serializer=new JavaScriptSerializer();
                    var latest=serializer.Deserialize<Dictionary<string,object>>(existing);
                    if(latest==null) throw new InvalidDataException("AI 配置不是 JSON 对象，请先修复 ai_config.json。");
                    var latestServer=Configuration.Object(latest,"server");
                    if(!SameModel(Value(latestServer,"model_path"),openedModel) || Value(latestServer,"active_preset")!=openedPreset)
                        throw new IOException("模型或预设已在其他窗口中切换，请关闭并重新打开路径配置后再保存。");
                    var writes=new List<KeyValuePair<string,string>>();
                    var merged=MergeConfig(existing,llama,mmproj);
                    if(audioProbe!=null)Configuration.Object(merged,"audio")["ffmpeg"]=audioProbe.Executable;
                    if(alignmentPaths!=null)alignmentPaths.Apply(merged,AlignmentPaths);
                    Configuration.Object(Configuration.Object(merged,"alignment"),"wav2vec2")["device"]=device;
                    writes.Add(new KeyValuePair<string,string>(configPath,serializer.Serialize(merged)));
                    if(File.Exists(installSettings)) {
                        var installation=ReadObject(installSettings,"{}"); installation["python_path"]=python;
                        writes.Add(new KeyValuePair<string,string>(installSettings,serializer.Serialize(installation)));
                    }
                    string preferences=Path.Combine(profileDirectory,"Preferences.txt");
                    writes.Add(new KeyValuePair<string,string>(preferences,Configuration.MergePreferences(File.Exists(preferences)?File.ReadAllText(preferences):"",aiDirectory,python)));
                    if(!string.IsNullOrEmpty(resultPath)) writes.Add(new KeyValuePair<string,string>(Path.GetFullPath(resultPath),python));
                    WriteTransaction(writes);
                    PythonPath=python; LlamaServerPath=llama; MmprojPath=mmproj;
                    RuntimeDevice=device;
                    if(audioProbe!=null)FfmpegPath=audioProbe.Executable;
                    AlignmentPaths=AIPraat.Setup.AlignmentPaths.Read(merged);
                    } finally {configGate.Unlock(0,1);}
                  }
                } finally {gate.Unlock(0,1);}
            }
        }
        void WriteTransaction(List<KeyValuePair<string,string>> writes)
        {
            string backup=Path.Combine(Path.GetDirectoryName(aiDirectory),".aipraat-backups","paths-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(backup);
            var originals=new Dictionary<string,byte[]>(); var manifest=new Dictionary<string,string>(); var changed=new List<string>();
            foreach(var write in writes) {
                byte[] bytes=File.Exists(write.Key)?File.ReadAllBytes(write.Key):null; originals.Add(write.Key,bytes);
                if(bytes!=null) {
                    string file=Path.Combine(backup,manifest.Count.ToString("D3")+".bak"); File.WriteAllBytes(file,bytes); manifest.Add(write.Key,file);
                }
            }
            File.WriteAllText(Path.Combine(backup,"manifest.json"),new JavaScriptSerializer().Serialize(manifest),utf8);
            try {
                foreach(var write in writes) {AtomicWrite(write.Key,utf8.GetBytes(write.Value));changed.Add(write.Key);}
            } catch(Exception originalError) {
                var failures=new List<Exception>();
                foreach(string path in changed.AsEnumerable().Reverse()) {
                    try {if(originals[path]==null) File.Delete(path);else AtomicWrite(path,originals[path]);} catch(Exception e) {failures.Add(e);}
                }
                if(failures.Count>0) throw new IOException("保存失败且部分文件无法恢复。原文件备份位于："+backup,originalError);
                throw;
            }
        }
        static void AtomicWrite(string path,byte[] content)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)); string temp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
            try {File.WriteAllBytes(temp,content);if(File.Exists(path)) File.Replace(temp,path,null);else File.Move(temp,path);}
            finally {if(File.Exists(temp)) File.Delete(temp);}
        }
    }
}
