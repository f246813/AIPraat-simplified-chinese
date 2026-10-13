using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Text.RegularExpressions;

namespace AIPraat.Setup
{
    public static class Configuration
    {
        public const string DefaultJson="{\"qwen\":{\"base_url\":\"http://127.0.0.1:8000/v1\",\"model\":\"local-model\",\"api_key\":\"EMPTY\",\"enable_thinking\":false,\"limit_tokens\":true,\"max_context_tokens\":32768,\"plan_max_tokens\":4096},\"api\":{\"enabled\":false,\"limit_tokens\":false,\"api_key\":\"\"},\"server\":{\"llama_server\":\"\",\"model_path\":\"\",\"mmproj_path\":\"\",\"host\":\"127.0.0.1\",\"port\":8000,\"n_gpu_layers\":-1,\"threads\":8,\"parallel\":1,\"auto_start\":false,\"presets\":[],\"active_preset\":\"\",\"mmproj_by_model\":{}},\"audio\":{\"ffmpeg\":\"\"},\"alignment\":{\"backend\":\"auto\",\"mfa\":{\"enabled\":false},\"wav2vec2\":{\"enabled\":false}}}";
        public static Dictionary<string,object> Object(Dictionary<string,object> root,string key)
        {
            object value;
            if(!root.TryGetValue(key,out value)|| !(value is Dictionary<string,object>))
                root[key]=new Dictionary<string,object>();
            return (Dictionary<string,object>)root[key];
        }
        public static Dictionary<string,object> MakeConfig(string existing,InstallRequest r,bool configure)
        {
            var json=new JavaScriptSerializer();
            var result=json.Deserialize<Dictionary<string,object>>(string.IsNullOrWhiteSpace(existing)?DefaultJson:existing);
            if(result==null) throw new InvalidDataException("已有 AI 配置不是 JSON 对象，请先备份并修复 ai_config.json。");
            if(configure) {
                Object(result,"audio")["ffmpeg"]=PathValidation.Clean(r.FfmpegPath);
                var server=Object(result,"server"); var qwen=Object(result,"qwen");
                string model=PathValidation.Clean(r.ModelPath), mmproj=PathValidation.Clean(r.MmprojPath);
                server["llama_server"]=PathValidation.Clean(r.LlamaServerPath);
                server["model_path"]=model; server["mmproj_path"]=mmproj;
                server["auto_start"]=model!="" && PathValidation.Clean(r.LlamaServerPath)!="";
                var bindings=Object(server,"mmproj_by_model");
                if(model!="") { if(mmproj!="") bindings[model]=mmproj; else bindings.Remove(model); }
                object saved;
                var presets=server.TryGetValue("presets",out saved) && saved is System.Collections.IEnumerable && !(saved is string)
                    ? ((System.Collections.IEnumerable)saved).Cast<object>().ToList() : new List<object>();
                server["active_preset"]="";
                if(model!="") {
                    var preset=presets.OfType<Dictionary<string,object>>().FirstOrDefault(x=>
                        x.ContainsKey("model_path") && string.Equals(Convert.ToString(x["model_path"]),model,StringComparison.OrdinalIgnoreCase));
                    if(preset==null) {
                        string id="installed-model"; int suffix=2;
                        while(presets.OfType<Dictionary<string,object>>().Any(x=>x.ContainsKey("id") && Convert.ToString(x["id"])==id)) id="installed-model-"+(suffix++);
                        preset=new Dictionary<string,object>{{"id",id},{"label",Path.GetFileName(model)},{"context_tokens",8192}};
                        presets.Add(preset);
                    }
                    if(!preset.ContainsKey("id")) preset["id"]="installed-model-"+Guid.NewGuid().ToString("N");
                    preset["model_path"]=model; preset["mmproj_path"]=mmproj; preset["vision"]=mmproj!="";
                    server["active_preset"]=preset["id"];
                }
                server["presets"]=presets.ToArray();
                if(model!="") qwen["model"]=Path.GetFileNameWithoutExtension(model);
                qwen["vision_when_requested"]=mmproj!="";
                if(r.AlignmentPaths!=null)r.AlignmentPaths.Apply(result,r.OriginalAlignmentPaths);
                Object(Object(result,"alignment"),"wav2vec2")["device"]=r.RuntimeDevice=="cuda"?"cuda":"cpu";
            }
            return result;
        }
        public static string MergePreferences(string text,string aiDirectory,string python)
        {
            var lines=Regex.Split(text??"","\r\n|\n|\r").Where(x=>x!="").ToList();
            SetPreference(lines,"AI.projectDirectory",aiDirectory);
            if(!string.IsNullOrEmpty(python)) SetPreference(lines,"Python.executablePath",python);
            return string.Join("\r\n",lines)+"\r\n";
        }
        static void SetPreference(List<string> lines,string key,string value)
        {
            lines.RemoveAll(x=>x.StartsWith(key+":",StringComparison.Ordinal));
            lines.Add(key+": "+value);
        }
        public static string ReadPythonPreference(string profile)
        {
            string path=Path.Combine(profile,"Preferences.txt");
            if(!File.Exists(path)) return "";
            foreach(string line in File.ReadAllLines(path))
                if(line.StartsWith("Python.executablePath:",StringComparison.Ordinal))
                    return line.Substring("Python.executablePath:".Length).Trim();
            return "";
        }
        public static string ChatPlugin(string installDirectory)
        {
            string command="\""+Path.Combine(installDirectory,"AIPraat.exe")+"\" --chat";
            return "# AIPraat 安装器生成；启动路径随安装目录配置。\nrunSystem: \""+
                command.Replace("\\","/").Replace("\"","\"\"")+"\"\n";
        }
    }
}
