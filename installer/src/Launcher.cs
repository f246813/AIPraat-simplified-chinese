using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Diagnostics;
using System.Windows.Forms;
using AIPraat.Setup;

public static class Launcher
{
    [STAThread]
    public static int Main(string[] args)
    {
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            string executable=Path.Combine(root,"Praat.exe");
            if(!File.Exists(executable)) throw new IOException("没有找到 Praat.exe，请重新运行安装器。");
            var settings=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"install-settings.json")));
            string python=settings.ContainsKey("python_path")?Convert.ToString(settings["python_path"]):"";
            string profile=InstallRequest.ForCurrentUser().ProfileDirectory;
            foreach(var p in Process.GetProcessesByName("Praat")) using(p) {
                if(args.Length==0) {
                    MessageBox.Show("请先关闭正在运行的 Praat，再从 AIPraat 快捷方式启动。","AIPraat",MessageBoxButtons.OK,MessageBoxIcon.Information);
                    return 1;
                }
            }
            // Runtime preparation supports the installing user and another user's own preferences.
            if(args.Length==0) InstallEngine.PrepareProfile(root,profile,python,
                (path,text,encoding)=>{ Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path,text,encoding); },
                (source,dest,count)=>{ Directory.CreateDirectory(Path.GetDirectoryName(dest)); File.Copy(source,dest,true); });
            var start=new ProcessStartInfo(executable) {UseShellExecute=false,WorkingDirectory=root};
            start.EnvironmentVariables["PRAAT_AI_PROJECT_DIR"]=Path.Combine(root,"ai");
            start.EnvironmentVariables["PRAAT_AI_INSTALL_ROOT"]=root;
            start.EnvironmentVariables["PRAAT_AI_CONFIG_PATH"]=Path.Combine(root,"ai","ai_config.json");
            start.EnvironmentVariables["PRAAT_AI_PRAAT_EXECUTABLE"]=executable;
            if(python!="") start.EnvironmentVariables["PRAAT_PYTHON_EXECUTABLE"]=python;
            if(args.Length>0 && args[0]=="--chat") {
                if(python=="") python=Configuration.ReadPythonPreference(profile);
                if(python=="" || !File.Exists(python)) throw new IOException("AI 前端尚未配置可用的 Python 路径，请重新运行安装器并选择“现在配置”。");
                var probe=PathValidation.ProbePython(python);
                if(!probe.Valid) throw new IOException(probe.Message);
                start.FileName=python;
                start.Arguments=PathValidation.QuoteArgument(Path.Combine(root,"ai","start_installed_frontend.py"));
                start.CreateNoWindow=true;
            }
            else start.Arguments=string.Join(" ",Array.ConvertAll(args,PathValidation.QuoteArgument));
            Process.Start(start); return 0;
        } catch(Exception e) {
            MessageBox.Show(e.Message,"AIPraat 启动提示",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1;
        }
    }
}
