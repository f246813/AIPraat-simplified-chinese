using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace AIPraat.Setup
{
    // One detected item of the alignment environment (launcher, dependency, temp
    // directory or configured model file).
    public sealed class MfaAlignmentCheck
    {
        public string id {get;set;}
        public string label {get;set;}
        public string status {get;set;}
        public string detail {get;set;}
        public string repair {get;set;}
        public bool ok {get{return string.Equals(status,"ok",StringComparison.OrdinalIgnoreCase);}}
    }

    public sealed class MfaAlignmentReport
    {
        public string schema {get;set;}
        public bool ready {get;set;}
        public string summary {get;set;}
        public string launcher {get;set;}
        public string environment {get;set;}
        public string version {get;set;}
        public string python {get;set;}
        public string[] blocking {get;set;}
        public MfaAlignmentCheck[] checks {get;set;}
        public string Message {get;set;}

        // The dialog shows every item with its own reason and repair, so a single
        // "unavailable" line never hides which dependency is actually missing.
        public string Describe()
        {
            if(checks==null||checks.Length==0)return Message??"未获得对齐环境检测结果。";
            var lines=new List<string>();
            if(!string.IsNullOrEmpty(Message))lines.Add(Message);
            foreach(var item in checks)
            {
                lines.Add((item.ok?"[可用] ":"[阻塞] ")+item.label+"："+(item.detail??""));
                if(!item.ok&&!string.IsNullOrEmpty(item.repair))lines.Add("        修复："+item.repair);
            }
            return string.Join("\r\n",lines);
        }

        public string FirstBlockingReason()
        {
            var item=(checks??new MfaAlignmentCheck[0]).FirstOrDefault(x=>!x.ok);
            if(item==null)return "";
            return item.label+"："+(item.detail??"");
        }
    }

    public static class MfaAlignmentProbe
    {
        public static MfaAlignmentReport Run(string python,string script,string mfaExecutable,string condaExecutable,
            string condaEnvironment,string dictionary,string acousticModel,int timeout,Action<string> progress)
        {
            python=PathValidation.Clean(python);
            if(string.IsNullOrEmpty(python)||!File.Exists(python))
                return new MfaAlignmentReport {Message="需要先选择可用的 Python 环境，才能检测对齐环境。"};
            if(string.IsNullOrEmpty(script)||!File.Exists(script))
                return new MfaAlignmentReport {Message="检测脚本缺失，请重新获取完整程序。"};
            string output=Path.Combine(Path.GetTempPath(),"AIPraat-mfa-probe-"+Guid.NewGuid().ToString("N")+".json");
            try
            {
                var arguments=new StringBuilder("-I "+PathValidation.QuoteArgument(script));
                arguments.Append(" --mfa-executable "+PathValidation.QuoteArgument(mfaExecutable??""));
                arguments.Append(" --conda-executable "+PathValidation.QuoteArgument(condaExecutable??""));
                arguments.Append(" --conda-environment "+PathValidation.QuoteArgument(condaEnvironment??""));
                arguments.Append(" --dictionary "+PathValidation.QuoteArgument(dictionary??""));
                arguments.Append(" --acoustic-model "+PathValidation.QuoteArgument(acousticModel??""));
                arguments.Append(" --timeout "+(timeout<=0?180:timeout));
                arguments.Append(" --output "+PathValidation.QuoteArgument(output));
                int exit=RuntimeDependencies.Execute(new ProcessStartInfo(python,arguments.ToString()),progress,timeout*1000+60000);
                if(!File.Exists(output))
                    return new MfaAlignmentReport {Message=exit==0?"检测未返回结果。":"对齐环境检测失败（退出码 "+exit+"）。"};
                var report=new JavaScriptSerializer().Deserialize<MfaAlignmentReport>(File.ReadAllText(output));
                if(report==null)return new MfaAlignmentReport {Message="对齐环境检测结果无法解析。"};
                report.Message=exit!=0&&report.ready?"检测退出码 "+exit+"，但结果标记为可用。":report.Message;
                return report;
            }
            catch(Exception error)
            {
                return new MfaAlignmentReport {Message="对齐环境检测失败："+error.Message};
            }
            finally
            {
                try {if(File.Exists(output))File.Delete(output);}catch(IOException){}catch(UnauthorizedAccessException){}
            }
        }
    }
}
