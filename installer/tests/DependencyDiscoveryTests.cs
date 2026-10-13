using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using AIPraat.Setup;

static class DependencyDiscoveryTests
{
    static int failures;
    static string python,ffmpeg,root,output;
    static readonly BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Case(string name,Action action){try{action();Console.WriteLine("PASS "+name);}catch(Exception error){failures++;Console.WriteLine("FAIL "+name+": "+(error.InnerException??error).Message);}}
    static void Wait(Func<bool> condition){var time=Stopwatch.StartNew();while(!condition()&&time.ElapsedMilliseconds<90000){Application.DoEvents();System.Threading.Thread.Sleep(20);}Assert(condition(),"asynchronous detection did not finish");}
    static void Render(Form form,string name){using(var image=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(image,new Rectangle(Point.Empty,form.Size));image.Save(Path.Combine(output,name));}}
    static void Fold(Form form,string name){var link=(LinkLabel)form.Controls.Find(name,true)[0];typeof(LinkLabel).GetMethod("OnLinkClicked",flags).Invoke(link,new object[]{new LinkLabelLinkClickedEventArgs(link.Links[0])});Application.DoEvents();}
    static void Archive(string path,string entry,string text){Directory.CreateDirectory(Path.GetDirectoryName(path));using(var zip=ZipFile.Open(path,ZipArchiveMode.Create))using(var writer=new StreamWriter(zip.CreateEntry(entry).Open()))writer.Write(text);}
    static Form Host(string ffmpegPath,out DependencyPanel panel){var form=new Form{ClientSize=new Size(870,690),Font=new Font("Microsoft YaHei UI",9.5f)};panel=new DependencyPanel(false,root);form.Controls.Add(panel);panel.Fill(python,ffmpegPath,"","","",new AlignmentPaths(),"cpu");return form;}
    [STAThread] static int Main(string[] args)
    {
        Console.OutputEncoding=new System.Text.UTF8Encoding(false);
        if(args.Length!=3||args.Any(string.IsNullOrWhiteSpace)) {
            Console.Error.WriteLine("Usage: DependencyDiscoveryTests.exe <python.exe> <ffmpeg.exe> <output-directory>");return 2;
        }
        try{return Run(args);}catch(Exception error){Console.Error.WriteLine("FAIL dependency test setup/run: "+error.GetType().FullName+": "+error.Message);return 1;}
    }
    static int Run(string[] args)
    {
        python=args[0];ffmpeg=args[1];output=args[2];Directory.CreateDirectory(output);root=Path.Combine(Path.GetTempPath(),"AIPraat-discovery-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);Application.EnableVisualStyles();
        string selected=Path.Combine(root,"selected"),outside=Path.Combine(root,"outside"),first=Path.Combine(selected,"one","ffmpeg.exe"),second=Path.Combine(selected,"two","ffmpeg.exe");
        foreach(string path in new[]{first,second,Path.Combine(outside,"ffmpeg.exe")}){Directory.CreateDirectory(Path.GetDirectoryName(path));File.Copy(ffmpeg,path);}
        string packages=Path.Combine(root,"packages"),wheel=Path.Combine(packages,"nested","numpy-2.0.0-py3-none-any.whl"),zip=Path.Combine(packages,"ffmpeg.zip");
        Archive(wheel,"numpy-2.0.0.dist-info/METADATA","Metadata-Version: 2.1\nName: numpy\nVersion: 2.0.0\n\n");Archive(zip,"bin/ffmpeg.exe","package fixture");File.WriteAllText(Path.Combine(packages,"broken.zip"),"not a ZIP");File.WriteAllText(Path.Combine(packages,"model.gguf"),"resource");
        Case("folder_recurses_only_selected_tree_and_preserves_multiple_verified_candidates",()=>{
            var result=DependencyDiscovery.Folder(selected);var programs=result.Candidates.Where(x=>x.Kind=="ffmpeg"&&!x.Package).ToArray();
            Assert(programs.Length==2&&programs.Any(x=>x.Path==first)&&programs.Any(x=>x.Path==second),"nested candidates were not both validated");Assert(result.Candidates.All(x=>!x.Path.StartsWith(outside)),"search escaped selected folder");
        });
        Case("automatic_keeps_valid_config_and_does_not_recurse_or_scan_packages",()=>{
            string oldPath=Environment.GetEnvironmentVariable("PATH");
            try{Environment.SetEnvironmentVariable("PATH",outside);var result=DependencyDiscovery.Automatic(new Dictionary<string,string>{{"python",python},{"ffmpeg",ffmpeg}},selected);
                Assert(result.Candidates.Single(x=>x.Kind=="ffmpeg").Path.Equals(ffmpeg,StringComparison.OrdinalIgnoreCase),"verified manual path replaced by PATH");Assert(result.Candidates.Single(x=>x.Kind=="python").Path.Equals(python,StringComparison.OrdinalIgnoreCase),"verified Python replaced");
                result=DependencyDiscovery.Automatic(new Dictionary<string,string>{{"python",python}},selected);
                Assert(result.Candidates.All(x=>x.Path!=first&&x.Path!=second&&!x.Package),"automatic search traversed subdirectories or packages");Assert(result.Candidates.Any(x=>x.Kind=="ffmpeg"&&x.Path==Path.Combine(outside,"ffmpeg.exe")),"PATH lookup missing");
            }finally{Environment.SetEnvironmentVariable("PATH",oldPath);}
        });
        Case("wheel_metadata_and_zip_are_packages_and_bad_archive_does_not_abort_search",()=>{
            var result=DependencyDiscovery.Folder(packages);
            Assert(result.Candidates.Any(x=>x.Kind=="numpy"&&x.Package&&x.Path==wheel),"wheel metadata not recognized");Assert(result.Candidates.Any(x=>x.Kind=="ffmpeg"&&x.Package&&x.Path==zip),"ZIP mistaken for installed program");Assert(result.Candidates.Any(x=>x.Kind=="resource")&&result.Notes.Any(x=>x.Contains("broken.zip")),"bad ZIP aborted resource discovery");
        });
        Case("package_found_keeps_required_gate_closed_and_does_not_pair_models",()=>{
            DependencyPanel panel;using(var form=Host(Path.Combine(root,"missing-ffmpeg.exe"),out panel)){
                form.Show();typeof(DependencyPanel).GetMethod("Search",flags).Invoke(panel,new object[]{packages});Wait(()=>!panel.Busy&&form.Controls.Find("FfmpegPathStatus",true)[0].Text.Contains("待安装"));
                Assert(!panel.Ready&&form.Controls.Find("ProcessFfmpegPath",true)[0].Visible,"package became ready before installation");Assert(panel.ModelPath==""&&panel.MmprojPath=="","resource filename inferred model pairing");
                var plan=(RuntimeInstallPlan)typeof(DependencyPanel).GetField("localPlan",flags).GetValue(panel);Assert(plan.packages["numpy"]==wheel&&plan.dependency_folders.Contains(Path.GetDirectoryName(wheel)),"recursive wheel sources were not passed to installer");Render(form,"dependencies-package-pending.png");form.Close();
            }
        });
        Case("multiple_program_candidates_use_user_choice_then_real_checks",()=>{
            DependencyPanel panel;using(var form=Host(Path.Combine(root,"missing-ffmpeg.exe"),out panel))using(var choice=new Timer{Interval=30}){
                bool prompted=false;choice.Tick+=(s,e)=>{foreach(Form dialog in Application.OpenForms.Cast<Form>().ToArray())if(dialog.Text=="选择 ffmpeg"){prompted=true;var list=dialog.Controls.OfType<ComboBox>().Single();list.SelectedItem=list.Items.Cast<DependencyCandidate>().Single(x=>x.Path==second);dialog.Controls.OfType<Button>().Single().PerformClick();choice.Stop();}};
                form.Show();choice.Start();typeof(DependencyPanel).GetMethod("Search",flags).Invoke(panel,new object[]{selected});Wait(()=>panel.Ready);
                Assert(prompted&&panel.FfmpegPath==second,"candidate chooser result not respected");Assert(!form.Controls.Find("ProcessFfmpegPath",true)[0].Visible,"ready dependency still has processing action");form.Close();
            }
        });
        Case("wizard_and_path_dialog_share_panel_gate_and_immediately_invalidate_path_edits",()=>{
            string install=Path.Combine(root,"install");Directory.CreateDirectory(Path.Combine(install,"ai"));File.WriteAllText(Path.Combine(install,"install-settings.json"),"{\"python_path\":\""+python.Replace("\\","\\\\")+"\"}");
            using(var form=new WizardForm(new InstallRequest{InstallDirectory=install},()=>null)){
                form.Show();((Button)form.Controls.Find("Next",true)[0]).PerformClick();var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];panel.Fill(python,ffmpeg,"","","",new AlignmentPaths(),"cpu");Wait(()=>panel.Ready);
                Assert(form.Controls.Find("Next",true)[0].Enabled&&form.Controls.Find("FindDependencies",true).Length==1,"wizard gate/search differs");var request=(InstallRequest)typeof(WizardForm).GetMethod("Gather",flags).Invoke(form,null);Assert(request.FfmpegPath==ffmpeg,"wizard omitted FFmpeg request");
                Render(form,"dependencies-wizard-ready.png");Fold(form,"OptionalTools");Fold(form,"ModelResources");
                foreach(string name in new[]{"LlamaServerPath","MfaExecutablePath","ModelPath","MmprojPath","Wav2Vec2ModelPath"}){var row=form.Controls.Find(name,true)[0].Parent;Assert(row.Width<=row.Parent.ClientSize.Width,"expanded row escapes viewport: "+name);}
                panel.ScrollControlIntoView(form.Controls.Find("ModelPath",true)[0].Parent.Parent);Render(form,"dependencies-wizard-expanded.png");
                form.Controls.Find("PythonPath",true)[0].Text=Path.Combine(root,"missing-python.exe");Assert(!panel.Ready&&!form.Controls.Find("Next",true)[0].Enabled,"old environment readiness persisted");form.Close();
            }
            var settings=new PathSettings(Path.Combine(root,"path-ai"),Path.Combine(root,"path-ai","ai_config.json"),Path.Combine(root,"profile"),python);
            using(var form=new PathSettingsForm(settings,"",0)){form.Show();var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];panel.Fill(python,ffmpeg,"","","",new AlignmentPaths(),"cpu");Wait(()=>panel.Ready);Render(form,"dependencies-paths-ready.png");form.Controls.Find("FfmpegPath",true)[0].Text=Path.Combine(root,"missing-ffmpeg.exe");Assert(!panel.Ready&&!form.Controls.Find("SavePaths",true)[0].Enabled,"old FFmpeg readiness persisted");form.Close();}
        });
        Case("both_hosts_remove_optional_processing_actions_and_retired_resource_fields",()=>{
            var settings=new PathSettings(Path.Combine(root,"actions-ai"),Path.Combine(root,"actions-ai","ai_config.json"),Path.Combine(root,"actions-profile"),python);
            foreach(bool wizard in new[]{false,true})using(Form form=wizard?(Form)new WizardForm(new InstallRequest{InstallDirectory=root},()=>null):new PathSettingsForm(settings,"",0)) {
                form.Show();if(wizard)((Button)form.Controls.Find("Next",true)[0]).PerformClick();
                var panel=(DependencyPanel)form.Controls.Find("DependencyPanel",true)[0];
                panel.Fill(Path.Combine(root,"missing-python.exe"),Path.Combine(root,"missing-ffmpeg.exe"),"","","",new AlignmentPaths(),"cpu");
                Fold(form,"OptionalTools");if(wizard)Fold(form,"ModelResources");
                var names=wizard?new[]{"LlamaServerPath","MfaExecutablePath","ModelPath","MmprojPath","Wav2Vec2ModelPath"}:new[]{"LlamaServerPath","MfaExecutablePath","MmprojPath"};
                foreach(string name in names)form.Controls.Find(name,true)[0].Text=Path.Combine(root,"missing-"+name);
                Wait(()=>names.All(name=>form.Controls.Find(name+"Status",true)[0].Text!="待检测"&&form.Controls.Find(name+"Status",true)[0].Text!="检测中…"));
                foreach(string name in names) {
                    Assert(form.Controls.Find("Process"+name,true).Length==0,"duplicate processing action remains: "+name);
                    Assert(form.Controls.Find("Browse"+name,true).Length==1,"browse entry removed: "+name);
                    var field=form.Controls.Find(name,true)[0];var browse=form.Controls.Find("Browse"+name,true)[0];
                    Assert(field.Right<browse.Left&&browse.Right<=field.Parent.Width,"single-action row layout overlaps: "+name);
                }
                foreach(string name in new[]{"MfaAcousticModelPath","MfaDictionaryPath"})Assert(form.Controls.Find(name,true).Length==0,"retired resource field remains: "+name);
                if(!wizard) {
                    foreach(string name in new[]{"ModelPath","ModelPathStatus","BrowseModelPath","Wav2Vec2ModelPath","Wav2Vec2ModelPathStatus","BrowseWav2Vec2ModelPath"})Assert(form.Controls.Find(name,true).Length==0,"ghost language model row remains: "+name);
                    Assert(form.Controls.Find("MmprojPath",true)[0].Parent.Top==form.Controls.Find("MfaExecutablePath",true)[0].Parent.Bottom,"removed model rows left an empty slot");
                }
                Assert(form.Controls.Find("RuntimePath",true).Length==0&&form.Controls.Find("ConfigureRuntime",true).Length==0&&form.Controls.Find("RuntimeDetails",true).Length==1,"duplicate runtime path or entry remains");
                if(wizard)Assert(form.Controls.Find("BrowseWav2Vec2ModelPath",true)[0].Text=="选择目录…"&&!((TextBox)form.Controls.Find("ModelPath",true)[0]).ReadOnly,"installer model inputs lost their real actions");
                Assert(form.Controls.Find("ProcessPythonPath",true).Length==1&&form.Controls.Find("ProcessFfmpegPath",true).Length==1,"installation actions were removed");
                panel.ScrollControlIntoView(form.Controls.Find("MmprojPath",true)[0].Parent.Parent);Render(form,wizard?"optional-actions-wizard.png":"optional-actions-paths.png");form.Close();
            }
        });
        Case("installer_checks_ffmpeg_before_filesystem_writes",()=>{
            var request=new InstallRequest{InstallDirectory=Path.Combine(root,"blocked-install"),ConfigurePrerequisites=true,PythonPath=python,FfmpegPath=Path.Combine(root,"missing-ffmpeg.exe")};bool blocked=false;
            try{new InstallEngine().Run(request,null,null);}catch(IOException){blocked=true;}Assert(blocked&&!Directory.Exists(request.InstallDirectory),"missing FFmpeg allowed installation");
            var config=Configuration.MakeConfig("{}",new InstallRequest{FfmpegPath=ffmpeg},true);Assert((string)Configuration.Object(config,"audio")["ffmpeg"]==ffmpeg,"installer config omitted FFmpeg");
        });
        Console.WriteLine("RESULT failures="+failures+"; fixtures="+root+"; renders="+output);return failures==0?0:1;
    }
}
