using System;
using System.IO;
using System.Text;
using System.Reflection;
using System.Diagnostics;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace AIPraat.Setup
{
    public sealed class RuntimeItem
    {
        public string id {get;set;}
        public string label {get;set;}
        public string requirement {get;set;}
        public string installed {get;set;}
        public string status {get;set;}
        public bool required {get;set;}
        public bool ok {get;set;}
    }
    public sealed class RuntimeReport
    {
        public string python {get;set;}
        public string version {get;set;}
        public string device {get;set;}
        public string message {get;set;}
        public string checked_at {get;set;}
        public string installation_error {get;set;}
        public string log_path {get;set;}
        public bool smoke {get;set;}
        public bool supported {get;set;}
        public bool ready {get;set;}
        public bool completed {get;set;}
        public RuntimeItem[] items {get;set;}
        public PythonProbe ToProbe()
        {
            bool valid=supported&&completed&&ready&&string.IsNullOrEmpty(installation_error)&&items!=null&&items.Length==RuntimeDependencies.Items().Length&&items.All(x=>x.ok);
            string text=!string.IsNullOrEmpty(installation_error)?"配置未完成："+installation_error:!supported?message:valid?"环境已就绪：Python "+version+"，全部必需运行库检测通过。":
                "未就绪："+string.Join("、",(items??new RuntimeItem[0]).Where(x=>!x.ok).Select(x=>x.label))+"。请配置运行库。";
            if(log_path=="")text+=" 日志未保存，不影响检测。";
            return new PythonProbe(valid,text,supported&&!valid) {Report=this};
        }
    }
    public sealed class RuntimeInstallPlan
    {
        public string[] dependency_folders=new string[0];
        public Dictionary<string,string> packages=new Dictionary<string,string>();
        public string dependency_folder="", webview2="", device="cpu";
        public RuntimeReport before;
    }
    public sealed class RuntimeInstallResult
    {
        public int ExitCode {get;set;}
        public PythonProbe Probe {get;set;}
        public string LogPath {get;set;}
        public string ResultPath {get;set;}
    }
    public sealed class RuntimeAssets : IDisposable
    {
        public readonly string Folder;
        public string Script {get{return Path.Combine(Folder,"runtime_dependencies.py");}}
        public string Requirements {get{return Path.Combine(Folder,"requirements.txt");}}
        public RuntimeAssets()
        {
            var resources=new Dictionary<string,string>();
            foreach(string name in new[]{"runtime_dependencies.py","requirements.txt","requirements-wav2vec2.txt","ConfigurePython.ps1"})resources.Add(name,RuntimeDependencies.Resource(name));
            Exception lastError=null;
            foreach(string parent in RuntimeDependencies.WorkRoots()) {
                Folder=Path.Combine(parent,"AIPraat-runtime-"+Guid.NewGuid().ToString("N"));
                try {
                    Directory.CreateDirectory(Folder);
                    foreach(var resource in resources)File.WriteAllText(Path.Combine(Folder,resource.Key),resource.Value,new UTF8Encoding(resource.Key.EndsWith(".ps1")));
                    return;
                }catch(UnauthorizedAccessException error){lastError=error;Dispose();}
                catch(IOException error){lastError=error;Dispose();}
            }
            throw new IOException("无法创建运行库检测工作目录。",lastError);
        }
        public void Dispose()
        {
            try {foreach(string file in Directory.GetFiles(Folder))File.Delete(file);Directory.Delete(Folder);}
            catch(IOException){}catch(UnauthorizedAccessException){}
        }
    }
    public static class RuntimeDependencies
    {
        internal static string[] WorkRoots()
        {
            return new[]{Path.GetTempPath(),Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"ai","runtime"),Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"logs","runtime","work")};
        }
        sealed class RuntimeLog : IDisposable
        {
            readonly object gate=new object();
            StreamWriter writer;
            public string Path {get;private set;}
            public RuntimeLog(string stem)
            {
                Path=string.IsNullOrEmpty(stem)?"":stem+".log";
                if(Path=="")return;
                try {writer=new StreamWriter(Path,false,new UTF8Encoding(false));}
                catch(UnauthorizedAccessException){Path="";}
                catch(IOException){Path="";}
            }
            public void Write(string line)
            {
                lock(gate) {
                    if(writer==null)return;
                    try {writer.WriteLine(DateTime.UtcNow.ToString("o")+" "+line);writer.Flush();}
                    catch(UnauthorizedAccessException){Path="";Dispose();}
                    catch(IOException){Path="";Dispose();}
                }
            }
            public void Dispose()
            {
                lock(gate) {
                    if(writer==null)return;
                    try {writer.Dispose();}
                    catch(UnauthorizedAccessException){Path="";}
                    catch(IOException){Path="";}
                    finally {writer=null;}
                }
            }
        }
        public static string LogStem(string operation)
        {
            return LogStem(operation,new[]{
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AIPraat","logs","runtime"),
                Path.Combine(Path.GetTempPath(),"AIPraat","logs","runtime"),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"logs","runtime")
            });
        }
        static string LogStem(string operation,string[] folders)
        {
            foreach(string folder in folders)try {
                Directory.CreateDirectory(folder);
                string stem=Path.Combine(folder,operation+"-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff")+"Z-"+Guid.NewGuid().ToString("N").Substring(0,8));
                // Logging is optional; verify file creation before choosing a destination.
                using(var file=new FileStream(stem+".log",FileMode.CreateNew,FileAccess.Write,FileShare.Read)){}
                return stem;
            }catch(UnauthorizedAccessException){}
            catch(IOException){}
            return "";
        }
        public static PythonProbe ReadProbe(string result,string python,string device,int exit)
        {
            if(!File.Exists(result))return new PythonProbe(false,"进程未返回完整运行库检测结果，请查看日志并重新检测。",true);
            var report=new JavaScriptSerializer().Deserialize<RuntimeReport>(File.ReadAllText(result));
            if(report==null||!report.completed||!string.Equals(Path.GetFullPath(report.python??""),Path.GetFullPath(python),StringComparison.OrdinalIgnoreCase)||report.device!=device)
                return new PythonProbe(false,"检测结果与所选 Python/设备不一致或未完成，请重新检测。",true);
            if(exit!=0)report.ready=false;
            return report.ToProbe();
        }
        public static string Resource(string name)
        {
            using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("AIPraat."+name)) {
                if(stream==null)throw new IOException("运行库资源缺失："+name+"。请重新获取完整程序。");
                using(var reader=new StreamReader(stream,Encoding.UTF8))return reader.ReadToEnd();
            }
        }
        public static RuntimeItem[] Items()
        {
            var rows=new List<RuntimeItem>();
            AddRequirements("requirements.txt",rows);
            rows.Add(new RuntimeItem{id="webview2",label="WebView2 Runtime",requirement="Microsoft Evergreen x64",required=true});
            rows.Add(new RuntimeItem{id="tk",label="Tcl/Tk",requirement="可创建 Tk 窗口",required=true});
            return rows.ToArray();
        }
        static void AddRequirements(string filename,List<RuntimeItem> rows)
        {
            foreach(string raw in Resource(filename).Split('\n')) {
                string line=raw.Split('#')[0].Trim();if(line=="")continue;
                if(line.StartsWith("-r ")){AddRequirements(line.Substring(3).Trim(),rows);continue;}
                string requirement=line.Split(';')[0].Trim();
                var match=System.Text.RegularExpressions.Regex.Match(requirement,@"^[\w.-]+");
                string id=match.Value.ToLowerInvariant().Replace('_','-');
                string label=id=="numpy"?"NumPy":id=="pillow"?"Pillow":id=="torch"?"PyTorch":id=="transformers"?"Transformers":id=="safetensors"?"Safetensors":id;
                rows.Add(new RuntimeItem{id=id,label=label,requirement=requirement,required=true});
            }
        }
        public static int Execute(ProcessStartInfo start,Action<string> progress,int timeout)
        {
            start.UseShellExecute=false;start.CreateNoWindow=true;start.RedirectStandardOutput=start.RedirectStandardError=true;
            start.StandardOutputEncoding=start.StandardErrorEncoding=Encoding.UTF8;
            start.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
            using(var process=new Process{StartInfo=start}) {
                DataReceivedEventHandler receive=(s,e)=>{if(e.Data!=null&&progress!=null)progress(e.Data);};
                process.OutputDataReceived+=receive;process.ErrorDataReceived+=receive;
                process.Start();process.BeginOutputReadLine();process.BeginErrorReadLine();
                if(!process.WaitForExit(timeout)) {process.Kill();process.WaitForExit();throw new IOException("运行库检测超时。请查看具体失败项后重试。");}
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        public static PythonProbe Probe(string python,string device,bool smoke,Action<string> progress)
        {
            python=PathValidation.Clean(python);
            if(!File.Exists(python)||!Path.GetFileName(python).Equals("python.exe",StringComparison.OrdinalIgnoreCase))
                return new PythonProbe(false,"请选择 Python 3.10–3.12 x64 的 python.exe（推荐 3.12）。");
            try {
                string stem=LogStem("detect");
                using(var log=new RuntimeLog(stem))
                using(var assets=new RuntimeAssets()) {
                    // The IPC result belongs to the working files, not the optional log directory.
                    string result=Path.Combine(assets.Folder,"probe.json");
                    Action<string> record=line=>{log.Write(line);if(progress!=null)progress(line);};
                    record("所选 Python："+python+"；设备："+device);
                    int exit=Execute(new ProcessStartInfo(python,"-I "+PathValidation.QuoteArgument(assets.Script)+" --device "+device+
                        " --output "+PathValidation.QuoteArgument(result)+(smoke?" --smoke":"")),record,600000);
                    var probe=ReadProbe(result,python,device,exit);
                    record(probe.Message);
                    if(log.Path!="")record("日志："+log.Path);
                    log.Dispose();
                    if(probe.Report!=null) {
                        probe.Report.log_path=log.Path;
                        if(log.Path!="")try {File.WriteAllText(stem+".json",new JavaScriptSerializer().Serialize(probe.Report),new UTF8Encoding(false));}
                        catch(UnauthorizedAccessException){if(progress!=null)progress("检测结果未保存，不影响检测。");}
                        catch(IOException){if(progress!=null)progress("检测结果未保存，不影响检测。");}
                        probe=probe.Report.ToProbe();
                    }
                    if(log.Path==""&&progress!=null)progress("日志未保存，不影响检测。");
                    return probe;
                }
            } catch(Exception e) {return new PythonProbe(false,"运行库检测失败："+e.Message,true);}
        }
        public static RuntimeInstallResult Install(string python,RuntimeInstallPlan plan,bool online,Action<string> progress)
        {
            string stem=LogStem("install");
            var result=new RuntimeInstallResult {LogPath=stem==""?"":stem+".log",ResultPath="",ExitCode=1};
            try {
                using(var assets=new RuntimeAssets()) {
                    string workingResult=Path.Combine(assets.Folder,"install-result.json");
                    string planPath=Path.Combine(assets.Folder,"local-plan.json");
                    File.WriteAllText(planPath,new JavaScriptSerializer().Serialize(plan),new UTF8Encoding(false));
                    string shell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe");
                    result.ExitCode=Execute(new ProcessStartInfo(shell,"-NoLogo -NoProfile -ExecutionPolicy Bypass -File "+
                        PathValidation.QuoteArgument(Path.Combine(assets.Folder,"ConfigurePython.ps1"))+" -PythonPath "+PathValidation.QuoteArgument(python)+
                        " -Mode "+(online?"Online":"Local")+" -Device "+plan.device+" -PlanPath "+PathValidation.QuoteArgument(planPath)+
                        " -ResultPath "+PathValidation.QuoteArgument(workingResult)+(result.LogPath==""?" -NoLog":" -LogPath "+PathValidation.QuoteArgument(result.LogPath))+" -NoPause"),progress,3600000);
                    result.Probe=ReadProbe(workingResult,python,plan.device,result.ExitCode);
                    result.LogPath=result.Probe.Report==null?"":result.Probe.Report.log_path??"";
                    if(result.LogPath!="")try {
                        string savedResult=Path.ChangeExtension(result.LogPath,".json");
                        File.Copy(workingResult,savedResult,true);result.ResultPath=savedResult;
                    }catch(UnauthorizedAccessException){if(progress!=null)progress("检测结果未保存，不影响检测。");}
                    catch(IOException){if(progress!=null)progress("检测结果未保存，不影响检测。");}
                }
            } catch(Exception e) {
                if(result.LogPath!="")try {File.AppendAllText(result.LogPath,DateTime.UtcNow.ToString("o")+" 配置失败："+e.Message+Environment.NewLine,new UTF8Encoding(false));}
                catch(UnauthorizedAccessException){result.LogPath="";}catch(IOException){result.LogPath="";}
                result.Probe=new PythonProbe(false,"配置失败："+e.Message+(result.LogPath==""?"。日志未保存。":"。日志："+result.LogPath),true);
            }
            return result;
        }
    }
}
