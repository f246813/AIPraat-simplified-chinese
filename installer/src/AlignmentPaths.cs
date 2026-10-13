using System;
using System.IO;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AIPraat.Setup
{
    // Paths only: enable flags, backend, Conda launcher and runtime parameters
    // remain owned by the existing alignment configuration.
    public sealed class AlignmentPaths
    {
        public string Wav2Vec2ModelPath {get;set;}
        public string MfaExecutablePath {get;set;}
        // MfaAcousticModelPath/MfaDictionaryPath are retained for config preservation; speech resource management writes them.
        public string MfaAcousticModelPath {get;set;}
        public string MfaDictionaryPath {get;set;}
        public AlignmentPaths(){Wav2Vec2ModelPath=MfaExecutablePath=MfaAcousticModelPath=MfaDictionaryPath="";}
        static string Value(Dictionary<string,object> data,string key,string fallback)
        {object value;return data.TryGetValue(key,out value)?Convert.ToString(value):fallback;}
        public static AlignmentPaths Read(Dictionary<string,object> config)
        {
            var alignment=Configuration.Object(config,"alignment");var wav=Configuration.Object(alignment,"wav2vec2");var mfa=Configuration.Object(alignment,"mfa");
            return new AlignmentPaths {Wav2Vec2ModelPath=Value(wav,"model",""),MfaExecutablePath=Value(mfa,"executable","mfa"),MfaAcousticModelPath=Value(mfa,"acoustic_model",""),MfaDictionaryPath=Value(mfa,"dictionary_path","")};
        }
        public void Apply(Dictionary<string,object> config,AlignmentPaths original)
        {
            var alignment=Configuration.Object(config,"alignment");var wav=Configuration.Object(alignment,"wav2vec2");var mfa=Configuration.Object(alignment,"mfa");
            Put(wav,"model",Wav2Vec2ModelPath,original==null?null:original.Wav2Vec2ModelPath);
            Put(mfa,"executable",MfaExecutablePath,original==null?null:original.MfaExecutablePath);
            Put(mfa,"acoustic_model",MfaAcousticModelPath,original==null?null:original.MfaAcousticModelPath);
            Put(mfa,"dictionary_path",MfaDictionaryPath,original==null?null:original.MfaDictionaryPath);
        }
        static void Put(Dictionary<string,object> section,string key,string value,string original)
        {
            string cleaned=PathValidation.Clean(value);
            if(original==null || cleaned!=PathValidation.Clean(original))section[key]=cleaned;
        }
        public string ValidationError()
        {
            string wav=PathValidation.Clean(Wav2Vec2ModelPath),exe=PathValidation.Clean(MfaExecutablePath),acoustic=PathValidation.Clean(MfaAcousticModelPath),dictionary=PathValidation.Clean(MfaDictionaryPath);
            if(wav!=""&&!Directory.Exists(wav)&&!( !Path.IsPathRooted(wav)&&Regex.IsMatch(wav,@"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)?$")))
                return "wav2vec2 模型目录不存在，请重新选择或清空此项。";
            if(exe!=""&&!Regex.IsMatch(exe,@"^mfa(\.(exe|cmd|bat))?$",RegexOptions.IgnoreCase)) {
                string ext=Path.GetExtension(exe);
                if(!Path.IsPathRooted(exe)||!File.Exists(exe)||!(ext.Equals(".exe",StringComparison.OrdinalIgnoreCase)||ext.Equals(".bat",StringComparison.OrdinalIgnoreCase)||ext.Equals(".cmd",StringComparison.OrdinalIgnoreCase)))
                    return "MFA 程序不存在，请选择 mfa.exe / .bat / .cmd，或清空此项。";
            }
            if(acoustic!=""&&!File.Exists(acoustic)&&!( !Path.IsPathRooted(acoustic)&&Regex.IsMatch(acoustic,@"^[A-Za-z0-9_-]+$")))
                return "MFA 声学模型文件不存在，请重新选择或清空此项。";
            if(dictionary!=""&&(!Path.IsPathRooted(dictionary)||!File.Exists(dictionary)))
                return "MFA 发音词典文件不存在，请重新选择或清空此项。";
            return null;
        }
        public string ValidationErrorForChanges(AlignmentPaths original)
        {
            if(original==null)return ValidationError();
            // An existing ignored/retired path must not block editing another
            // field. New or changed paths still receive the full validation.
            return new AlignmentPaths {
                Wav2Vec2ModelPath=Changed(Wav2Vec2ModelPath,original.Wav2Vec2ModelPath),
                MfaExecutablePath=Changed(MfaExecutablePath,original.MfaExecutablePath),
                MfaAcousticModelPath=Changed(MfaAcousticModelPath,original.MfaAcousticModelPath),
                MfaDictionaryPath=Changed(MfaDictionaryPath,original.MfaDictionaryPath)
            }.ValidationError();
        }
        static string Changed(string value,string original){return PathValidation.Clean(value)==PathValidation.Clean(original)?"":value;}
    }
}
