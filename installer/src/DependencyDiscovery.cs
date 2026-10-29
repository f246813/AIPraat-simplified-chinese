using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using Microsoft.Win32;

namespace AIPraat.Setup
{
    public sealed class DependencyCandidate
    {
        public string Kind, Path, Version, Description;
        public bool Package;
        public override string ToString(){return Version+" · "+Path;}
    }
    public sealed class DependencySearchResult
    {
        public readonly List<DependencyCandidate> Candidates=new List<DependencyCandidate>();
        public readonly List<string> Notes=new List<string>();
    }
    // Automatic lookup only visits explicit directories. Recursive lookup is
    // reserved for the directory selected by the user, without following links.
    public static class DependencyDiscovery
    {
        public static DependencyCandidate ProbeProgram(string kind,string path)
        {
            path=PathValidation.Clean(path);
            if(!System.IO.Path.IsPathRooted(path)||!File.Exists(path))return null;
            try {
                if(kind=="ffmpeg") {
                    var audio=FfmpegDependencies.Probe(path);
                    return audio.Ready?new DependencyCandidate{Kind=kind,Path=audio.Executable,Version=audio.Version,Description=audio.Message}:null;
                }
                var output=new StringBuilder();var gate=new object();
                string args=kind=="python"?"-I -c \"import sys,struct; print('Python '+sys.version.split()[0]); sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) and struct.calcsize('P')==8 else 1)\"":"--version";
                ProcessStartInfo start;
                if(kind=="mfa"&&(System.IO.Path.GetExtension(path).Equals(".bat",StringComparison.OrdinalIgnoreCase)||System.IO.Path.GetExtension(path).Equals(".cmd",StringComparison.OrdinalIgnoreCase)))
                    start=new ProcessStartInfo(Environment.GetEnvironmentVariable("COMSPEC")??"cmd.exe","/d /s /c \""+PathValidation.QuoteArgument(path)+" --version\"");
                else start=new ProcessStartInfo(path,args);
                int exit=RuntimeDependencies.Execute(start,line=>{lock(gate){output.AppendLine(line);}},30000);
                string text=output.ToString().Trim();
                bool recognized=kind=="python"?text.StartsWith("Python 3.",StringComparison.OrdinalIgnoreCase):kind=="llama"?text.IndexOf("version",StringComparison.OrdinalIgnoreCase)>=0||text.IndexOf("build",StringComparison.OrdinalIgnoreCase)>=0:
                    text.IndexOf("Montreal",StringComparison.OrdinalIgnoreCase)>=0||text.IndexOf("mfa",StringComparison.OrdinalIgnoreCase)>=0||System.Text.RegularExpressions.Regex.IsMatch(text,@"^\d+\.\d+\.\d+");
                if(exit!=0||!recognized)return null;
                return new DependencyCandidate{Kind=kind,Path=System.IO.Path.GetFullPath(path),Version=text.Split('\n')[0].Trim(),Description=text};
            } catch(Exception){return null;}
        }
        public static IEnumerable<string> RegisteredPython()
        {
            var paths=new List<string>();
            foreach(var hive in new[]{RegistryHive.CurrentUser,RegistryHive.LocalMachine})
            foreach(var view in new[]{RegistryView.Registry64,RegistryView.Registry32}) {
                try {
                    using(var root=RegistryKey.OpenBaseKey(hive,view))using(var python=root.OpenSubKey(@"Software\Python")) {
                        if(python==null)continue;
                        foreach(string company in python.GetSubKeyNames().Where(x=>x!="PyLauncher"))using(var vendor=python.OpenSubKey(company)) {
                            foreach(string tag in vendor.GetSubKeyNames())using(var install=vendor.OpenSubKey(tag+@"\InstallPath")) {
                                if(install==null)continue;
                                // Registered interpreters with required custom arguments
                                // cannot be represented by our executable-only settings.
                                if(Convert.ToString(install.GetValue("ExecutableArguments","")).Trim()!="")continue;
                                string executable=Convert.ToString(install.GetValue("ExecutablePath",""));
                                if(executable==""&&company.Equals("PythonCore",StringComparison.OrdinalIgnoreCase))executable=System.IO.Path.Combine(Convert.ToString(install.GetValue("","")),"python.exe");
                                if(executable!="")paths.Add(executable);
                            }
                        }
                    }
                } catch(System.Security.SecurityException){}catch(UnauthorizedAccessException){}catch(IOException){}
            }
            return paths.Distinct(StringComparer.OrdinalIgnoreCase);
        }
        public static DependencySearchResult Automatic(IDictionary<string,string> configured,string programDirectory)
        {
            var result=new DependencySearchResult();
            var directories=new List<string>{programDirectory,AppDomain.CurrentDomain.BaseDirectory,FfmpegDependencies.InstallDirectory};
            foreach(string relative in new[]{"python","python/Scripts",".venv/Scripts","ai/venv/Scripts","tools","tools/ffmpeg","tools/miniforge3/envs/aligner/Scripts","llama.cpp"})directories.Add(System.IO.Path.Combine(programDirectory,relative));
            directories.AddRange((Environment.GetEnvironmentVariable("PATH")??"").Split(System.IO.Path.PathSeparator).Where(x=>!string.IsNullOrWhiteSpace(x)).Select(x=>x.Trim('"')));
            foreach(string kind in new[]{"python","ffmpeg","llama","mfa"}) {
                string current;configured.TryGetValue(kind,out current);
                var existing=ProbeProgram(kind,current??"");
                if(existing!=null){result.Candidates.Add(existing);continue;}
                var paths=new List<string>();
                string name=kind=="python"?"python.exe":kind=="llama"?"llama-server.exe":kind=="ffmpeg"?"ffmpeg.exe":"mfa.exe";
                foreach(string directory in directories) {
                    try {paths.Add(System.IO.Path.Combine(directory,name));if(kind=="mfa")foreach(string ext in new[]{"bat","cmd"})paths.Add(System.IO.Path.Combine(directory,"mfa."+ext));}catch(ArgumentException){}
                }
                if(kind=="python")paths.AddRange(RegisteredPython());
                foreach(string path in paths.Distinct(StringComparer.OrdinalIgnoreCase)) {
                    var candidate=ProbeProgram(kind,path);if(candidate!=null)result.Candidates.Add(candidate);
                }
            }
            return result;
        }
        public static DependencySearchResult Folder(string selected)
        {
            var result=new DependencySearchResult();var pending=new Stack<string>();pending.Push(System.IO.Path.GetFullPath(selected));
            while(pending.Count>0) {
                string directory=pending.Pop();
                try {
                    if((File.GetAttributes(directory)&FileAttributes.ReparsePoint)!=0){result.Notes.Add("跳过目录链接："+directory);continue;}
                    foreach(string child in Directory.GetDirectories(directory))pending.Push(child);
                    foreach(string file in Directory.GetFiles(directory)) {
                        try {
                        if((File.GetAttributes(file)&FileAttributes.ReparsePoint)!=0)continue;
                        string name=System.IO.Path.GetFileName(file).ToLowerInvariant();
                        string kind=name=="python.exe"?"python":name=="ffmpeg.exe"?"ffmpeg":name=="llama-server.exe"?"llama":name=="mfa.exe"||name=="mfa.bat"||name=="mfa.cmd"?"mfa":null;
                        if(kind!=null){var program=ProbeProgram(kind,file);if(program!=null)result.Candidates.Add(program);else result.Notes.Add("程序验证未通过："+file);continue;}
                        if(name.EndsWith(".whl"))InspectWheel(file,result);
                        else if(name.EndsWith(".zip")) {
                            using(var archive=ZipFile.OpenRead(file)) {
                                if(archive.Entries.Count(x=>x.Name.Equals("ffmpeg.exe",StringComparison.OrdinalIgnoreCase))==1)
                                    result.Candidates.Add(new DependencyCandidate{Kind="ffmpeg",Path=file,Package=true,Version="ZIP 安装包",Description="已找到安装包，尚未安装及检测；需发布方 SHA-256"});
                            }
                        } else if(name=="microsoftedgewebview2runtimeinstallerx64.exe")result.Candidates.Add(new DependencyCandidate{Kind="webview2",Path=file,Package=true,Version="安装包",Description="安装时验证微软数字签名"});
                        else if(name.StartsWith("python-")&&name.EndsWith(".exe")) {
                            var info=FileVersionInfo.GetVersionInfo(file);
                            if((info.ProductName??"").IndexOf("Python",StringComparison.OrdinalIgnoreCase)>=0)result.Candidates.Add(new DependencyCandidate{Kind="python",Path=file,Package=true,Version=info.ProductVersion,Description="Python 安装包，尚未安装为可用环境"});
                        } else if(name.EndsWith(".gguf")||name.EndsWith(".dict"))result.Candidates.Add(new DependencyCandidate{Kind="resource",Path=file,Version="资源文件",Description="仅记录路径，需在资源管理中明确用途及配套关系"});
                        } catch(InvalidDataException error){result.Notes.Add(file+"："+error.Message);}catch(IOException error){result.Notes.Add(file+"："+error.Message);}catch(UnauthorizedAccessException error){result.Notes.Add(file+"："+error.Message);}

                    }
                }catch(IOException error){result.Notes.Add(directory+"："+error.Message);}catch(UnauthorizedAccessException error){result.Notes.Add(directory+"："+error.Message);}
            }
            return result;
        }
        static void InspectWheel(string file,DependencySearchResult result)
        {
            using(var zip=ZipFile.OpenRead(file)) {
                var entries=zip.Entries.Where(x=>x.FullName.EndsWith(".dist-info/METADATA",StringComparison.Ordinal)).ToArray();
                if(entries.Length!=1){result.Notes.Add("wheel 元数据不完整："+file);return;}
                string name="",version="";using(var reader=new StreamReader(entries[0].Open())) {
                    string line;while((line=reader.ReadLine())!=null&&line!="") {if(line.StartsWith("Name: "))name=line.Substring(6).Trim();if(line.StartsWith("Version: "))version=line.Substring(9).Trim();}
                }
                string normalized=name.Replace('_','-').ToLowerInvariant();
                var item=RuntimeDependencies.Items().FirstOrDefault(x=>x.id.Replace('_','-')==normalized||x.requirement.Split('[','<','>','=','!','~')[0].Replace('_','-').Equals(normalized,StringComparison.OrdinalIgnoreCase));
                result.Candidates.Add(new DependencyCandidate{Kind=item==null?"wheel":item.id,Path=file,Version=name+" "+version,Package=true,Description="wheel 安装包；兼容性、版本约束和间接依赖在安装前验证"});
            }
        }
    }
}
