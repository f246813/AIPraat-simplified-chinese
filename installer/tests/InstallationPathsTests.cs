using System;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Diagnostics;
using System.Collections.Generic;
using AIPraat.Setup;

static class InstallationPathsTests
{
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static int Main(string[] args)
    {
        try {
            string root=Path.GetFullPath(args[0]);Directory.CreateDirectory(root);
            RuntimeDependencies.InstallRoot=root;
            Assert(PythonSetup.CreateStartInfo("script.ps1",args[2]).Arguments.Contains("-InstallRoot "+PathValidation.QuoteArgument(root)),"standalone runtime setup lost target directory");
            string stem=RuntimeDependencies.LogStem("paths");
            Assert(Path.GetDirectoryName(stem)==Path.Combine(root,"logs","runtime"),"runtime logs escaped target directory");
            using(var assets=new RuntimeAssets())Assert(Path.GetDirectoryName(assets.Folder)==Path.Combine(root,"cache","temp"),"working assets escaped target directory");
            string archive=Path.Combine(root,"ffmpeg.zip");
            using(var zip=ZipFile.Open(archive,ZipArchiveMode.Create))zip.CreateEntryFromFile(args[1],"bin/ffmpeg.exe");
            string hash;using(var sha=SHA256.Create())using(var stream=File.OpenRead(archive))hash=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","");
            var result=FfmpegDependencies.Install(Console.WriteLine,archive,hash);
            Assert(result.Ready,result.Message);
            Assert(result.Executable==Path.Combine(root,"tools","ffmpeg","ffmpeg.exe"),"FFmpeg installed outside target directory");
            Assert(Directory.GetFiles(Path.Combine(root,"logs","ffmpeg"),"*.log").Length==1,"FFmpeg log missing");
            Assert(Directory.GetDirectories(Path.Combine(root,"cache","temp")).Length==0,"download/work directories leaked");
            var lines=new List<string>();
            int exit=RuntimeDependencies.Execute(new ProcessStartInfo(args[2],"-I -c \"import os,sys; sys.stdout.reconfigure(encoding='utf-8'); print(os.environ['PRAAT_AI_INSTALL_ROOT']); print(os.environ['HF_HOME'])\""),lines.Add,15000);
            Assert(exit==0&&lines.Contains(root)&&lines.Contains(Path.Combine(root,"cache/huggingface")),"child process lost selected installation root");
            string blocked=Path.Combine(root,"blocked");File.WriteAllText(blocked,"fixture");
            RuntimeDependencies.InstallRoot=blocked;
            using(var assets=new RuntimeAssets())Assert(Path.GetDirectoryName(assets.Folder)==Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar),"unwritable target did not retain temporary fallback");
            Console.WriteLine("PASS installation-relative logs, working files, child cache environment, offline FFmpeg installation and fallback");
            return 0;
        }catch(Exception error){Console.Error.WriteLine(error);return 1;}
    }
}
