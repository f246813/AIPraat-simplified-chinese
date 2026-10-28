using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Web.Script.Serialization;

namespace AIPraat.Setup
{
    public sealed class InstallEngine
    {
        sealed class Change { public string Path, Backup; }
        readonly List<Change> changes=new List<Change>();
        readonly List<string> createdDirectories=new List<string>();
        string stage;
        long completedBytes,totalBytes;
        int lastPercent;
        Action<InstallProgress> report;
        public static Stream OpenPayload()
        {
            var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("AIPraat.Payload.zip");
            if(stream==null) throw new InvalidDataException("安装包缺少程序资源，请重新获取完整安装包。");
            return stream;
        }
        void Progress(int value,string message)
        {
            lastPercent=Math.Max(lastPercent,Math.Min(100,value));
            if(report!=null) report(new InstallProgress(lastPercent,message));
        }
        void EnsureDirectory(string path)
        {
            if(Directory.Exists(path)) return;
            string parent=Path.GetDirectoryName(path);
            if(!string.IsNullOrEmpty(parent)) EnsureDirectory(parent);
            Directory.CreateDirectory(path); createdDirectories.Add(path);
        }
        void Backup(string destination)
        {
            PathValidation.CheckReparseAncestors(destination);
            if(changes.Any(x=>x.Path.Equals(destination,StringComparison.OrdinalIgnoreCase))) return;
            var c=new Change {Path=destination};
            if(File.Exists(destination)) {
                c.Backup=Path.Combine(stage,"backups",changes.Count.ToString("D4")+".bak");
                Directory.CreateDirectory(Path.GetDirectoryName(c.Backup));
                File.Copy(destination,c.Backup);
            }
            changes.Add(c);
        }
        void Copy(string source,string destination,bool countBytes)
        {
            if(countBytes)Progress(lastPercent,"正在安装："+Path.GetFileName(destination));
            Backup(destination); EnsureDirectory(Path.GetDirectoryName(destination));
            using(var input=File.OpenRead(source))
            using(var output=new FileStream(destination,FileMode.Create,FileAccess.Write,FileShare.None)) {
                byte[] buffer=new byte[128*1024]; int read;
                while((read=input.Read(buffer,0,buffer.Length))>0) {
                    output.Write(buffer,0,read);
                    if(countBytes) { completedBytes+=read; Progress(5+(int)(completedBytes*84/Math.Max(1,totalBytes)), "正在安装："+Path.GetFileName(destination)); }
                }
            }
        }
        void Write(string path,string content,Encoding encoding)
        {
            Backup(path); EnsureDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path,content,encoding);
        }
        static string SafeEntry(string root,string name)
        {
            if(string.IsNullOrEmpty(name)||Path.IsPathRooted(name)||name.Contains(":") ||
                name.Split('/','\\').Any(x=>x==".." || x=="."))
                throw new InvalidDataException("安装资源路径无效："+name);
            string full=Path.GetFullPath(Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar)));
            if(!full.StartsWith(root.TrimEnd('\\')+"\\",StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("安装资源越过目标目录："+name);
            return full;
        }
        static string CreateWorkingDirectory()
        {
            Exception lastError=null;
            foreach(string parent in RuntimeDependencies.WorkRoots()) {
                string folder=Path.Combine(parent,"AIPraat-install-"+Guid.NewGuid().ToString("N"));
                try {
                    Directory.CreateDirectory(folder);string check=Path.Combine(folder,"write-check");
                    using(var file=File.Create(check)){}File.Delete(check);return folder;
                }catch(UnauthorizedAccessException error){lastError=error;}
                catch(IOException error){lastError=error;}
                try{if(Directory.Exists(folder))Directory.Delete(folder,true);}catch(UnauthorizedAccessException){}catch(IOException){}
            }
            throw new IOException("无法创建可写的安装工作目录。",lastError);
        }
        static string Hash(string file)
        {
            using(var sha=SHA256.Create()) using(var input=File.OpenRead(file))
                return BitConverter.ToString(sha.ComputeHash(input)).Replace("-","").ToLowerInvariant();
        }
        public void Run(InstallRequest request,Stream payload,Action<InstallProgress> progress)
        {
            changes.Clear(); createdDirectories.Clear(); report=progress; lastPercent=0; completedBytes=0;
            string error=PathValidation.InstallDirectoryError(request.InstallDirectory);
            if(error!=null) throw new IOException(error);
            string root=Path.GetFullPath(PathValidation.Clean(request.InstallDirectory)).TrimEnd('\\');
            request.InstallDirectory=root;
            if(!request.ConfigurePrerequisites)throw new IOException("安装前必须配置 Python 和全部必需运行库。");
            RuntimeDependencies.InstallRoot=root;
            var runtimeProbe=RuntimeDependencies.Probe(request.PythonPath,request.RuntimeDevice=="cuda"?"cuda":"cpu",true,
                message=>Progress(0,message));
            if(!runtimeProbe.Valid)throw new IOException(runtimeProbe.Message);
            var audioProbe=FfmpegDependencies.Probe(request.FfmpegPath);
            if(!audioProbe.Ready)throw new IOException(audioProbe.Message);
            request.FfmpegPath=audioProbe.Executable;
            if(request.ConfigurePrerequisites) {
                error=PathValidation.OptionalPathsError(request);
                if(error!=null) throw new IOException(error);
                if(!File.Exists(request.PythonPath)) throw new IOException("Python 路径已失效，请返回重新配置。");
            }
            foreach(var p in Process.GetProcessesByName("Praat")) using(p) {
                try {
                    if(p.MainModule.FileName.Equals(Path.Combine(root,"Praat.exe"),StringComparison.OrdinalIgnoreCase))
                        throw new IOException("请先关闭这个安装目录中的 Praat，再重新安装。");
                } catch(System.ComponentModel.Win32Exception) { }
            }
            stage=CreateWorkingDirectory();
            bool succeeded=false; DirectorySecurity previousAcl=null;
            try {
                Progress(0,"准备安装文件…");
                var extracted=new List<KeyValuePair<string,string>>();
                string unpack=Path.Combine(stage,"payload"); Directory.CreateDirectory(unpack);
                using(var archive=new ZipArchive(payload,ZipArchiveMode.Read,true)) {
                    totalBytes=archive.Entries.Where(x=>x.Name!="").Sum(x=>x.Length)*2;
                    foreach(var entry in archive.Entries) {
                        string dest=SafeEntry(unpack,entry.FullName);
                        if(entry.Name=="") { Directory.CreateDirectory(dest); continue; }
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        using(var input=entry.Open()) using(var output=File.Create(dest)) {
                            byte[] buffer=new byte[128*1024]; int read;
                            while((read=input.Read(buffer,0,buffer.Length))>0) {
                                output.Write(buffer,0,read); completedBytes+=read;
                                Progress(5+(int)(completedBytes*84/Math.Max(1,totalBytes)), "正在准备："+entry.FullName);
                            }
                        }
                        extracted.Add(new KeyValuePair<string,string>(entry.FullName,dest));
                    }
                }
                if(!extracted.Any(x=>x.Key=="Praat.exe")||!extracted.Any(x=>x.Key=="AIPraat.exe"))
                    throw new InvalidDataException("安装资源不完整：缺少 Praat 程序。");
                string manifest=Path.Combine(unpack,"payload-manifest.json");
                if(File.Exists(manifest)) {
                    var expected=new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(manifest));
                    foreach(var entry in extracted.Where(x=>x.Key!="payload-manifest.json"))
                        if(!expected.ContainsKey(entry.Key)||!Hash(entry.Value).Equals(expected[entry.Key],StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException("安装资源校验失败："+entry.Key);
                }
                EnsureDirectory(root);
                foreach(var entry in extracted) Copy(entry.Value,SafeEntry(root,entry.Key),true);
                Progress(90,"保存文件路径与 AI 配置…");
                string configPath=Path.Combine(root,"ai","ai_config.json");
                string config=File.Exists(configPath)?File.ReadAllText(configPath):Configuration.DefaultJson;
                // Reinstallation without configuration preserves the existing JSON byte for byte.
                if(request.ConfigurePrerequisites || !File.Exists(configPath))
                    Write(configPath,new JavaScriptSerializer().Serialize(Configuration.MakeConfig(config,request,request.ConfigurePrerequisites)),new UTF8Encoding(false));
                var settings=new Dictionary<string,object>();
                string settingsPath=Path.Combine(root,"install-settings.json");
                if(File.Exists(settingsPath)) {
                    settings=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(settingsPath));
                    if(settings==null) throw new InvalidDataException("已有 install-settings.json 无效，请先修复。");
                }
                if(request.ConfigurePrerequisites) settings["python_path"]=request.PythonPath;
                if(!settings.ContainsKey("python_path")) settings["python_path"]="";
                settings["installed_at"]=DateTime.UtcNow.ToString("o");
                settings["install_directory"]=root;
                Write(settingsPath,new JavaScriptSerializer().Serialize(settings),new UTF8Encoding(false));
                string selectedPython=Convert.ToString(settings["python_path"]);
                PrepareProfile(root,request.ProfileDirectory,selectedPython,Write,Copy);
                Progress(95,"创建启动快捷方式…");
                CreateShortcut(Path.Combine(request.ProgramsDirectory,"AIPraat.lnk"),root);
                if(request.CreateDesktopShortcut) CreateShortcut(Path.Combine(request.DesktopDirectory,"AIPraat.lnk"),root);
                if(!string.IsNullOrEmpty(request.UserSid)) {
                    previousAcl=Directory.GetAccessControl(root);
                    var acl=Directory.GetAccessControl(root);
                    acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(request.UserSid),
                        FileSystemRights.Modify|FileSystemRights.Synchronize,
                        InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
                    Directory.SetAccessControl(root,acl);
                }
                var backups=changes.Where(x=>x.Backup!=null).ToList();
                if(backups.Count>0) {
                    string destination=Path.Combine(root,".aipraat-backups",DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,8));
                    PathValidation.CheckReparseAncestors(destination); EnsureDirectory(destination);
                    foreach(var c in backups) File.Copy(c.Backup,Path.Combine(destination,Path.GetFileName(c.Backup)));
                    File.WriteAllText(Path.Combine(destination,"index.json"),new JavaScriptSerializer().Serialize(
                        backups.Select(x=>new Dictionary<string,string>{{"path",x.Path},{"backup",Path.GetFileName(x.Backup)}}).ToArray()),new UTF8Encoding(false));
                }
                succeeded=true; Progress(100,"安装已完成");
            } catch(Exception original) {
                var rollbackErrors=new List<string>();
                foreach(var change in changes.AsEnumerable().Reverse()) {
                    try {
                        if(change.Backup!=null) File.Copy(change.Backup,change.Path,true);
                        else if(File.Exists(change.Path)) File.Delete(change.Path);
                    } catch(Exception e) { rollbackErrors.Add(e.Message); }
                }
                if(previousAcl!=null) try { Directory.SetAccessControl(root,previousAcl); } catch(Exception e) { rollbackErrors.Add(e.Message); }
                foreach(string dir in createdDirectories.AsEnumerable().Reverse())
                    try { if(Directory.Exists(dir)&&!Directory.EnumerateFileSystemEntries(dir).Any()) Directory.Delete(dir); } catch { }
                if(rollbackErrors.Count>0) throw new IOException(original.Message+"\n部分文件未能恢复，请保留备份："+Path.Combine(stage,"backups"),original);
                throw;
            } finally {
                // Retain failed backup material when rollback could not restore it.
                bool canClean=succeeded;
                if(!canClean) try { canClean=changes.All(x=>x.Backup==null || (File.Exists(x.Path)&&Hash(x.Path)==Hash(x.Backup))); } catch { }
                if(canClean)
                    try { Directory.Delete(stage,true); } catch { }
            }
        }
        public delegate void Writer(string path,string text,Encoding encoding);
        public delegate void Copier(string source,string dest,bool count);
        public static void PrepareProfile(string root,string profile,string python,Writer write,Copier copy)
        {
            string prefs=Path.Combine(profile,"Preferences.txt");
            string text=File.Exists(prefs)?File.ReadAllText(prefs):"";
            write(prefs,Configuration.MergePreferences(text,Path.Combine(root,"ai"),python),Encoding.Unicode);
            string source=Path.Combine(root,"ai","plugin","praat_ai"), destination=Path.Combine(profile,"plugin_praat_ai");
            foreach(string name in new[]{"setup.praat","praatAiMeasure.praat","praatAiMeasureEditor.praat"}) {
                string file=Path.Combine(source,name);
                if(File.Exists(file)) copy(file,Path.Combine(destination,name),false);
            }
            write(Path.Combine(destination,"praatAiChat.praat"),Configuration.ChatPlugin(root),new UTF8Encoding(false));
        }
        void CreateShortcut(string target,string root)
        {
            string source=Path.Combine(stage,Guid.NewGuid().ToString("N")+".lnk");
            object shell=null,shortcut=null;
            try {
                shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
                shortcut=shell.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{source});
                shortcut.GetType().InvokeMember("TargetPath",BindingFlags.SetProperty,null,shortcut,new object[]{Path.Combine(root,"AIPraat.exe")});
                shortcut.GetType().InvokeMember("WorkingDirectory",BindingFlags.SetProperty,null,shortcut,new object[]{root});
                shortcut.GetType().InvokeMember("Description",BindingFlags.SetProperty,null,shortcut,new object[]{"AIPraat 语音分析与 AI 前端"});
                shortcut.GetType().InvokeMember("IconLocation",BindingFlags.SetProperty,null,shortcut,new object[]{Path.Combine(root,"Praat.exe")+",0"});
                shortcut.GetType().InvokeMember("Save",BindingFlags.InvokeMethod,null,shortcut,null);
                Copy(source,target,false);
            } finally {
                if(shortcut!=null) Marshal.FinalReleaseComObject(shortcut);
                if(shell!=null) Marshal.FinalReleaseComObject(shell);
            }
        }
    }
}
