import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {previewSidebarTransform} from './preview-sidebar-transform.mjs';
const frontendURL=new URL('../../../../ai/frontend/',import.meta.url);
const {createServer}=await import(new URL('node_modules/vite/dist/node/index.js',frontendURL));
const output=path.dirname(fileURLToPath(import.meta.url));
const root=fileURLToPath(frontendURL);
const fixture=`
  sections.push({id:'preview-work',name:'稳定早期版',appearance:null,builtin:false},{id:'preview-pronunciation',name:'发音练习',appearance:null,builtin:false});
  sessions[0].title='英语送气音分析';sessions[0].sectionId='preview-work';sessions[0].sectionPosition=1000000;
  sessions[2].title='长对话与图表记录';
  sessions.push({id:'preview-pinned',title:'常用测量流程',created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null,pinned:true,sectionId:pinnedId,sectionPosition:1000000});
  sessions.push({id:'preview-vowels',title:'元音音高与时长',created:now,updated:now,readOnly:false,draft:'',scroll:0,anchor:null,sectionId:'preview-pronunciation',sectionPosition:1000000});
  messages['preview-pinned']=[];messages['preview-vowels']=[];
  messages['demo-1']=[{id:'preview-user',role:'user',content:'帮我测量这段录音中 /p/ 的 VOT，并说明应该如何确定起止位置。'},{id:'preview-answer',role:'assistant',status:'complete',content:'可以先定位爆破释放，再寻找后续元音开始稳定振动的位置。\\n\\n### 先确定两个时间点\\n\\n**起点**放在爆破释放的瞬间；**终点**放在周期性声带振动开始的位置。结合波形和频谱一起判断，边界会更清楚。\\n\\n> VOT = 起声时间 − 爆破释放时间\\n\\n放大到毫秒尺度，再核对两个边界。这是一段用于检查排版的示例回复，未执行真实测量。'}];
`;
const formula=String.raw`> $$
> \mathrm{VOT} = t_{\text{起声}} - t_{\text{爆破释放}}
> $$`;
const styledFixture=fixture.replace('> VOT = 起声时间 − 爆破释放时间',()=>JSON.stringify(formula).slice(1,-1));
const server=await createServer({root,configFile:path.join(root,'vite.config.ts'),server:{host:'127.0.0.1',port:5191,strictPort:true},plugins:[{name:'preview-sidebar-layout',enforce:'pre',transform:previewSidebarTransform},{
 name:'appearance-preview-only',enforce:'post',
 configureServer(s){s.middlewares.use('/__appearance-preview.css',(_req,res)=>{res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(path.join(output,'apple-webview-preview.css'),'utf8'));});},
 transformIndexHtml(){return [{tag:'link',attrs:{rel:'stylesheet',href:'/__appearance-preview.css'},injectTo:'body'}];},
 transform(code,id){if(id.replaceAll('\\','/').endsWith('/src/demo.ts'))return code.replace(/conversation_width:\s*850/g,'conversation_width:720').replace('const organization =',()=>styledFixture+'const organization =').replace('const organization=',()=>styledFixture+'const organization=');}
}]});
await server.listen();
console.log('Preview only: http://127.0.0.1:5191/?demo=1');
