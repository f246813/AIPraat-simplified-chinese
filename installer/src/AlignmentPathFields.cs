using System;
using System.Collections.Generic;
using System.Windows.Forms;

namespace AIPraat.Setup
{
    public sealed class AlignmentPathFields
    {
        readonly TextBox wav=new TextBox(),exe=new TextBox(),acoustic=new TextBox(),dictionary=new TextBox();
        public event EventHandler Changed;
        public Control[] Add(Panel page,int top,ToolTip tips,bool includeMfaResources=true,bool includeWav2Vec2=true)
        {
            var controls=new List<Control>();
            if(includeWav2Vec2) {
                controls.AddRange(FilePathField.Add(page,wav,"wav2vec2 模型（可选）","选择本地模型目录，可留空",top,"Wav2Vec2ModelPath",null,tips));
                top+=79;
            }
            controls.AddRange(FilePathField.Add(page,exe,"MFA 程序（可选）","选择 mfa.exe，可留空",top,"MfaExecutablePath","MFA 程序|mfa.exe;*.bat;*.cmd|可执行文件|*.exe|所有文件|*.*",tips));
            if(includeMfaResources) {
                controls.AddRange(FilePathField.Add(page,acoustic,"MFA 声学模型（可选）","选择声学模型 .zip，可留空",top+79,"MfaAcousticModelPath","MFA 声学模型|*.zip|所有文件|*.*",tips));
                controls.AddRange(FilePathField.Add(page,dictionary,"MFA 发音词典（可选）","选择发音词典 .dict / .txt，可留空",top+158,"MfaDictionaryPath","MFA 发音词典|*.dict;*.txt;*.zip|所有文件|*.*",tips));
            }
            tips.SetToolTip(wav,"选择本地模型目录；已有模型 ID 会保留。");tips.SetToolTip(acoustic,"选择声学模型 .zip；已有 MFA 模型名称会保留。");
            foreach(var field in new[]{wav,exe,acoustic,dictionary})field.TextChanged+=(s,e)=>{var handler=Changed;if(handler!=null)handler(this,EventArgs.Empty);};
            return controls.ToArray();
        }
        public AlignmentPaths Read(){return new AlignmentPaths {Wav2Vec2ModelPath=wav.Text,MfaExecutablePath=exe.Text,MfaAcousticModelPath=acoustic.Text,MfaDictionaryPath=dictionary.Text};}
        public void Fill(AlignmentPaths paths){wav.Text=paths.Wav2Vec2ModelPath;exe.Text=paths.MfaExecutablePath;acoustic.Text=paths.MfaAcousticModelPath;dictionary.Text=paths.MfaDictionaryPath;}
    }
}
