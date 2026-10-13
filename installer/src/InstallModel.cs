using System;
using System.IO;
using System.Security.Principal;

namespace AIPraat.Setup
{
    public sealed class InstallRequest
    {
        public string InstallDirectory { get; set; }
        public bool ConfigurePrerequisites { get; set; }
        public string PythonPath { get; set; }
        public string FfmpegPath { get; set; }
        public string LlamaServerPath { get; set; }
        public string ModelPath { get; set; }
        public string MmprojPath { get; set; }
        public AlignmentPaths AlignmentPaths { get; set; }
        public AlignmentPaths OriginalAlignmentPaths { get; set; }
        public string ProfileDirectory { get; set; }
        public string ProgramsDirectory { get; set; }
        public string DesktopDirectory { get; set; }
        public string UserSid { get; set; }
        public bool CreateDesktopShortcut { get; set; }
        public string RuntimeDevice { get; set; }
        public InstallRequest() { PythonPath=FfmpegPath=LlamaServerPath=ModelPath=MmprojPath=""; }
        public static InstallRequest ForCurrentUser()
        {
            return new InstallRequest { InstallDirectory=PathValidation.DefaultDirectory,
                ProfileDirectory=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),"Praat"),
                ProgramsDirectory=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"AIPraat"),
                DesktopDirectory=Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                UserSid=WindowsIdentity.GetCurrent().User.Value, CreateDesktopShortcut=true };
        }
        public void ValidateUserDestinations(string appData,string programs,string desktop)
        {
            string[] actual={ProfileDirectory,ProgramsDirectory,DesktopDirectory};
            string[] expected={Path.Combine(appData,"Praat"),Path.Combine(programs,"AIPraat"),desktop};
            for(int i=0;i<actual.Length;i++)
                if(!string.Equals(Path.GetFullPath(actual[i]).TrimEnd('\\'),Path.GetFullPath(expected[i]).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("安装恢复文件中的用户目录与该用户的实际目录不一致。");
        }
    }
    public sealed class InstallProgress
    {
        public int Percent { get; private set; }
        public string Message { get; private set; }
        public InstallProgress(int percent,string message) { Percent=percent; Message=message; }
    }
    public sealed class PythonProbe
    {
        public bool Valid { get; private set; }
        public string Message { get; private set; }
        public bool CanConfigure { get; private set; }
        public RuntimeReport Report { get; set; }
        public PythonProbe(bool valid,string message) : this(valid,message,false) { }
        public PythonProbe(bool valid,string message,bool canConfigure) { Valid=valid; Message=message; CanConfigure=canConfigure; }
    }
}
