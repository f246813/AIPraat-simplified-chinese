using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Text;

namespace AIPraat.Setup
{
    public static class PythonSetup
    {
        public static ProcessStartInfo CreateStartInfo(string script,string python)
        {
            string shell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe");
            return new ProcessStartInfo(shell,"-NoLogo -NoProfile -ExecutionPolicy Bypass -File "+PathValidation.QuoteArgument(script)+" -PythonPath "+PathValidation.QuoteArgument(python)) {
                UseShellExecute=true,WindowStyle=ProcessWindowStyle.Normal
            };
        }
        public static int Run(string python)
        {
            using(var assets=new RuntimeAssets()) {
                using(var process=Process.Start(CreateStartInfo(Path.Combine(assets.Folder,"ConfigurePython.ps1"),python))) {
                    process.WaitForExit();return process.ExitCode;
                }
            }
        }
    }
}
