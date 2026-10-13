using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Drawing;
using System.Windows.Forms;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Web.Script.Serialization;

static class InstallerWindowProductionTests
{
    static readonly BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
    static string root,fixture,python,ffmpeg;
    static Assembly assembly;
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static object Private(object target,string name){return target.GetType().GetField(name,flags).GetValue(target);}
    static void Set(object target,string name,object value){target.GetType().GetProperty(name).SetValue(target,value,null);}
    static void Capture(Form form,string name){using(var bitmap=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(bitmap,new Rectangle(Point.Empty,form.Size));bitmap.Save(Path.Combine(root,"test-records","installer",name));}}
    static void Wait(Func<bool> done,Action tick=null){var watch=System.Diagnostics.Stopwatch.StartNew();while(!done()&&watch.ElapsedMilliseconds<180000){Application.DoEvents();if(tick!=null)tick();System.Threading.Thread.Sleep(20);}Assert(done(),"real installation workflow timed out");}
    static Form Wizard(string target)
    {
        var request=Activator.CreateInstance(assembly.GetType("AIPraat.Setup.InstallRequest"));
        foreach(var item in new[]{new[]{"InstallDirectory",target},new[]{"ProfileDirectory",Path.Combine(fixture,"profile-"+Path.GetFileName(target))},new[]{"ProgramsDirectory",Path.Combine(fixture,"programs-"+Path.GetFileName(target))},new[]{"DesktopDirectory",Path.Combine(fixture,"desktop-"+Path.GetFileName(target))},new[]{"PythonPath",python},new[]{"FfmpegPath",ffmpeg},new[]{"RuntimeDevice","cpu"},new[]{"UserSid",WindowsIdentity.GetCurrent().User.Value}})Set(request,item[0],item[1]);
        Set(request,"CreateDesktopShortcut",true);
        return (Form)Activator.CreateInstance(assembly.GetType("AIPraat.Setup.WizardForm"),new object[]{request,new Func<Stream>(()=>assembly.GetManifestResourceStream("AIPraat.Payload.zip"))});
    }
    static void Prepare(Form form,bool capture)
    {
        form.Show();if(capture)Capture(form,"delivered-install-directory.png");
        ((Button)form.Controls.Find("Next",true)[0]).PerformClick();var panel=form.Controls.Find("DependencyPanel",true)[0];
        Wait(()=>(bool)panel.GetType().GetProperty("Ready").GetValue(panel,null));
        foreach(string name in new[]{"ModelPath","Wav2Vec2ModelPath"})Assert(form.Controls.Find(name,true).Length==1&&!((TextBox)form.Controls.Find(name,true)[0]).ReadOnly,"installer model field missing or read-only: "+name);
        Assert(form.Controls.Find("BrowseWav2Vec2ModelPath",true)[0].Text=="选择目录…","wav2vec2 is not a directory action");
        Assert(form.Controls.Find("RuntimePath",true).Length==0&&form.Controls.Find("ConfigureRuntime",true).Length==0&&form.Controls.Find("RuntimeDetails",true).Length==1,"runtime has duplicate path or entry");
        string model=Path.Combine(fixture,"wav2vec2-model");Directory.CreateDirectory(model);form.Controls.Find("Wav2Vec2ModelPath",true)[0].Text=model;
        var fold=(LinkLabel)form.Controls.Find("ModelResources",true)[0];if(fold.Text.StartsWith("▸"))typeof(LinkLabel).GetMethod("OnLinkClicked",flags).Invoke(fold,new object[]{new LinkLabelLinkClickedEventArgs(fold.Links[0])});
        Wait(()=>form.Controls.Find("Wav2Vec2ModelPathStatus",true)[0].Text=="本地模型目录");
        Assert(form.Controls.Find("Next",true)[0].Text=="开始安装"&&form.Controls.Find("Next",true)[0].Enabled,"ready installer action missing");
        if(capture){Capture(form,"delivered-install-environment.png");var old=form.Size;form.Size=form.MinimumSize;Application.DoEvents();Assert(!((ScrollableControl)panel).HorizontalScroll.Visible,"narrow installer has horizontal overflow");Capture(form,"delivered-install-environment-small.png");form.Size=old;}
        Console.WriteLine("PASS delivered installer has editable language model, wav2vec2 directory selection and real 13/13 readiness");
    }
    static void Install(Form form,bool expectFailure,bool capture)
    {
        bool captured=false;((Button)form.Controls.Find("Next",true)[0]).PerformClick();
        Wait(()=>!(bool)Private(form,"installing"),()=>{if(capture&&!captured){var progress=(ProgressBar)Private(form,"bar");if(progress.Value>0&&progress.Value<100){Assert(!form.Controls.Find("Next",true)[0].Enabled&&!form.Controls.Find("Cancel",true)[0].Enabled,"write-in-progress actions are enabled");Capture(form,"delivered-install-progress.png");captured=true;}}});
        if(expectFailure){Assert((bool)Private(form,"failure"),"permission fixture unexpectedly installed");var error=(Label)Private(form,"installationError");Assert((bool)Private(form,"requireElevation"),"failure was not the actual directory permission error: "+error.Text);Assert(error.Visible&&error.Parent.Height>=TextRenderer.MeasureText(error.Text,error.Font,new Size(error.Width-24,1000),TextFormatFlags.WordBreak).Height+24,"permission error text is clipped");Assert(form.Controls.Find("RetryAsAdministrator",true)[0].Visible,"permission recovery action missing");Capture(form,"delivered-install-failure.png");Console.WriteLine("PASS real directory write rejection exposes readable error and recovery actions");}
        else {Assert((bool)Private(form,"finished"),"real installation failed: "+((Label)Private(form,"installationError")).Text);Assert(form.Controls.Find("Next",true)[0].Text=="完成","completed installer has no Finish action");if(capture)Capture(form,"delivered-install-complete.png");Console.WriteLine("PASS real embedded payload installation reaches completion");}
    }
    static void RunWindow(Form form,Action<Form> workflow)
    {
        Exception failure=null;
        form.Shown+=(s,e)=>form.BeginInvoke(new Action(()=>{try{workflow(form);}catch(Exception error){failure=error;Console.WriteLine("FLOW ERROR "+error.GetType().FullName+": "+error.Message);Wait(()=>!(bool)Private(form,"installing"));}finally{form.Close();}}));
        Application.Run(form);if(failure!=null)throw failure;
    }
    [STAThread]static int Main(string[] args)
    {
        try{
            root=AppDomain.CurrentDomain.BaseDirectory;python=args[0];ffmpeg=args[1];fixture=Path.Combine(root,"scratch","delivered-install-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(fixture);Application.EnableVisualStyles();assembly=Assembly.Load(File.ReadAllBytes(Path.Combine(root,"AIPraat-install.exe")));
            string target=Path.Combine(fixture,"installed");using(var form=Wizard(target))RunWindow(form,window=>{Prepare(window,true);Install(window,false,true);});
            Assert(File.Exists(Path.Combine(target,"AIPraat-paths.exe")),"delivered path program not installed");
            var config=new JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string,object>>(File.ReadAllText(Path.Combine(target,"ai","ai_config.json")));var alignment=(System.Collections.Generic.Dictionary<string,object>)config["alignment"];var wav=(System.Collections.Generic.Dictionary<string,object>)alignment["wav2vec2"];Assert((string)wav["model"]==Path.Combine(fixture,"wav2vec2-model"),"installer lost chosen wav2vec2 directory");
            string deniedTarget=Path.Combine(fixture,"denied");Directory.CreateDirectory(deniedTarget);var original=Directory.GetAccessControl(deniedTarget);var denied=Directory.GetAccessControl(deniedTarget);denied.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,FileSystemRights.CreateFiles,AccessControlType.Deny));Directory.SetAccessControl(deniedTarget,denied);
            try{using(var form=Wizard(deniedTarget))RunWindow(form,window=>{Prepare(window,false);Install(window,true,false);Restore(deniedTarget,original);Install(window,false,false);Console.WriteLine("PASS retry after restoring fixture permissions completes real installation");});}finally{Restore(deniedTarget,original);}
            Console.WriteLine("RESULT production installer workflow passed; fixture="+fixture);return 0;
        }catch(Exception error){Console.WriteLine("FAIL "+(error.InnerException??error).Message);return 1;}
    }
    static void Restore(string target,DirectorySecurity original){var restored=new DirectorySecurity();restored.SetSecurityDescriptorSddlForm(original.GetSecurityDescriptorSddlForm(AccessControlSections.Access),AccessControlSections.Access);Directory.SetAccessControl(target,restored);}
}
