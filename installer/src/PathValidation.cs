using System;
using System.IO;
using System.Diagnostics;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Collections;
using System.Collections.Generic;

namespace AIPraat.Setup
{
    public static class PathValidation
    {
        public const string DefaultDirectory=@"C:\AIPraat";
        public static string Clean(string value)
        {
            return Environment.ExpandEnvironmentVariables((value??"").Trim().Trim('"'));
        }
        public static string InstallDirectoryError(string value)
        {
            try {
                value=Clean(value);
                if(string.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value)) return "请输入完整安装路径，例如 C:\\AIPraat。";
                string full=Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
                if(full==Path.GetPathRoot(full).TrimEnd(Path.DirectorySeparatorChar)) return "请选择驱动器里的文件夹，例如 C:\\AIPraat。";
                if(full.Length>180) return "安装路径过长，请选择更短的文件夹路径（最多 180 字符）。";
                if(value.IndexOfAny(Path.GetInvalidPathChars())>=0 || full.Substring(Path.GetPathRoot(full).Length).Split('\\','/')
                    .AnyInvalidPart()) return "安装路径含有无效的文件夹名称。";
                if(File.Exists(full)) return "此路径是文件，请选择文件夹。";
                CheckReparseAncestors(full);
                return null;
            } catch(Exception e) { return "安装路径不可用："+e.Message; }
        }
        static bool AnyInvalidPart(this string[] parts)
        {
            foreach(string p in parts) {
                if(p.IndexOfAny(Path.GetInvalidFileNameChars())>=0 || p.EndsWith(" ") || p.EndsWith(".")) return true;
                string stem=p.Split('.')[0].ToUpperInvariant();
                if(stem=="CON" || stem=="PRN" || stem=="AUX" || stem=="NUL" ||
                    System.Text.RegularExpressions.Regex.IsMatch(stem,@"^(COM|LPT)[1-9]$")) return true;
            }
            return false;
        }
        public static void CheckReparseAncestors(string path)
        {
            string cursor=Path.GetFullPath(path);
            while(!string.IsNullOrEmpty(cursor)) {
                if((Directory.Exists(cursor)||File.Exists(cursor)) && (File.GetAttributes(cursor)&FileAttributes.ReparsePoint)!=0)
                    throw new IOException("不能安装到符号链接或目录联接："+cursor);
                cursor=Path.GetDirectoryName(cursor);
            }
        }
        public static string OptionalPathsError(InstallRequest r)
        {
            string[] paths={r.LlamaServerPath,r.ModelPath,r.MmprojPath};
            string[] labels={"llama-server","前端模型","视觉投影"};
            string[] extensions={".exe",".gguf",".gguf"};
            for(int i=0;i<paths.Length;i++) {
                string p=Clean(paths[i]);
                if(p=="") continue;
                if(!Path.IsPathRooted(p)||!File.Exists(p)) return labels[i]+"文件不存在，请重新选择或清空此项。";
                if(!Path.GetExtension(p).Equals(extensions[i],StringComparison.OrdinalIgnoreCase)) return labels[i]+"应选择 "+extensions[i]+" 文件。";
                if(i==0 && !Path.GetFileName(p).Equals("llama-server.exe",StringComparison.OrdinalIgnoreCase))
                    return "llama.cpp 路径应指向 llama-server.exe。";
            }
            return r.AlignmentPaths==null?null:r.AlignmentPaths.ValidationErrorForChanges(r.OriginalAlignmentPaths);
        }
        public static string QuoteArgument(string text)
        {
            var result=new System.Text.StringBuilder("\""); int slashes=0;
            foreach(char c in text) {
                if(c=='\\') { slashes++; continue; }
                if(c=='"') result.Append('\\',slashes*2+1).Append('"');
                else result.Append('\\',slashes).Append(c);
                slashes=0;
            }
            return result.Append('\\',slashes*2).Append('"').ToString();
        }
        public static PythonProbe ProbePython(string path)
        {
            return RuntimeDependencies.Probe(path,"cpu",true,null);
        }
    }
}
