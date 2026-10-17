using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Text;

namespace AIPraat.Setup
{
    public sealed class FfmpegProbe
    {
        public bool Ready;
        public string Executable="", Message="", Version="";
    }
    public static class FfmpegDependencies
    {
        public static string InstallDirectory {get{return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AIPraat","ffmpeg");}}
        public static string Resolve(string configured)
        {
            configured=PathValidation.Clean(configured);
            if(configured!="")return configured;
            string managed=Path.Combine(InstallDirectory,"ffmpeg.exe");
            if(File.Exists(managed))return managed;
            foreach(string directory in (Environment.GetEnvironmentVariable("PATH")??"").Split(Path.PathSeparator)) {
                try {string candidate=Path.Combine(directory.Trim('"'),"ffmpeg.exe");if(File.Exists(candidate))return Path.GetFullPath(candidate);}catch(ArgumentException){}
            }
            return "";
        }
        public static FfmpegProbe Probe(string configured)
        {
            var result=new FfmpegProbe();
            string temporary="";
            try {
                result.Executable=Resolve(configured);
                if(result.Executable==""||!Path.IsPathRooted(result.Executable)||!File.Exists(result.Executable)) {
                    result.Message="未找到可用 FFmpeg，请选择 ffmpeg.exe 或点击“安装 FFmpeg…”。";return result;
                }
                var output=new StringBuilder();var gate=new object();
                Action<string> record=line=>{lock(gate){output.AppendLine(line);}};
                int exit=RuntimeDependencies.Execute(new ProcessStartInfo(result.Executable,"-nostdin -version"),record,15000);
                result.Version=output.ToString().Split('\n')[0].Trim();
                if(exit!=0||!result.Version.StartsWith("ffmpeg version ",StringComparison.OrdinalIgnoreCase))
                    throw new IOException("所选程序不是可用的 FFmpeg。"+output);
                using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("AIPraat.ffmpeg-probe.ogg")) {
                    if(stream==null)throw new IOException("音频检测样本缺失。");
                    Exception lastError=null;
                    foreach(string parent in RuntimeDependencies.WorkRoots()) {
                        temporary=Path.Combine(parent,"aipraat-ffmpeg-probe-"+Guid.NewGuid().ToString("N"));
                        try {
                            Directory.CreateDirectory(temporary);stream.Position=0;
                            using(var file=File.Create(Path.Combine(temporary,"probe.ogg")))stream.CopyTo(file);
                            break;
                        }catch(UnauthorizedAccessException error){lastError=error;}
                        catch(IOException error){lastError=error;}
                        try{Directory.Delete(temporary,true);}catch(IOException){}catch(UnauthorizedAccessException){}
                        temporary="";
                    }
                    if(temporary=="")throw new IOException("无法创建音频检测工作目录。",lastError);
                }
                string source=Path.Combine(temporary,"probe.ogg"),wav=Path.Combine(temporary,"decoded.wav");
                output.Clear();
                exit=RuntimeDependencies.Execute(new ProcessStartInfo(result.Executable,"-nostdin -v error -i "+PathValidation.QuoteArgument(source)+
                    " -map 0:a:0 -vn -c:a pcm_s24le -y "+PathValidation.QuoteArgument(wav)),record,30000);
                if(exit!=0||!File.Exists(wav)||new FileInfo(wav).Length<=44)throw new IOException("实际音频解码检测失败。"+output);
                using(var reader=new BinaryReader(File.OpenRead(wav))) {
                    if(Encoding.ASCII.GetString(reader.ReadBytes(4))!="RIFF")throw new IOException("解码输出不是 WAV。");
                    reader.ReadInt32();if(Encoding.ASCII.GetString(reader.ReadBytes(4))!="WAVE")throw new IOException("解码输出不是 WAV。");
                }
                result.Ready=true;result.Message="已就绪："+result.Version+"；实际 OGG → WAV 解码通过。";
            } catch(Exception error) {result.Message="不可用："+error.Message;}
            finally {if(temporary!="")try{Directory.Delete(temporary,true);}catch(IOException){}catch(UnauthorizedAccessException){}}
            return result;
        }
        public static FfmpegProbe Install(Action<string> progress,string archivePath="",string expectedSha256="")
        {
            string folder=Path.Combine(Path.GetTempPath(),"aipraat-ffmpeg-install-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(folder);
            string log="";
            try {
                string logs=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AIPraat","logs","ffmpeg");
                Directory.CreateDirectory(logs);log=Path.Combine(logs,"install-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff")+"Z-"+Guid.NewGuid().ToString("N").Substring(0,8)+".log");
                string script=Path.Combine(folder,"Install-FFmpeg.ps1");
                File.WriteAllText(script,RuntimeDependencies.Resource("Install-FFmpeg.ps1"),new UTF8Encoding(true));
                string shell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe");
                var output=new StringBuilder();var gate=new object();
                Action<string> record=line=>{lock(gate){output.AppendLine(line);File.AppendAllText(log,line+Environment.NewLine,new UTF8Encoding(false));}if(progress!=null)progress(line);};
                int exit=RuntimeDependencies.Execute(new ProcessStartInfo(shell,"-NoLogo -NoProfile -ExecutionPolicy Bypass -File "+PathValidation.QuoteArgument(script)+
                    " -InstallDirectory "+PathValidation.QuoteArgument(InstallDirectory)+(archivePath!=""?" -ArchivePath "+PathValidation.QuoteArgument(archivePath)+" -ExpectedSha256 "+PathValidation.QuoteArgument(expectedSha256):"")),record,900000);
                if(exit!=0)return new FfmpegProbe {Message="安装失败，完整日志："+log};
                return Probe(Path.Combine(InstallDirectory,"ffmpeg.exe"));
            } catch(Exception error) {return new FfmpegProbe {Message="安装失败："+error.Message+(log!=""?"；日志："+log:"")};}
            finally {try{Directory.Delete(folder,true);}catch(IOException){}catch(UnauthorizedAccessException){}}
        }
    }
}
