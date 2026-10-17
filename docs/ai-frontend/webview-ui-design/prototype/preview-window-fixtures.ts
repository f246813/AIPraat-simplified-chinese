// Only appended to main.tsx in the isolated preview Vite server.
// Uses the project's existing offline fixtures. No model, disk, or host actions.
import {mfaFixture, bootstrapFixture} from '../tests/mfa-fixtures';
const previewMfa = structuredClone(mfaFixture);
previewMfa.theme = 'light';
let previewAnki = {media_dir:'',ffmpeg:'',fallback:true,audio_field:'Audio',start_field:'Start',end_field:'End',mode:'both',magnifier_selector:'.search-link'};
const previewResourceLists = {
 dictionaries:[{name:'English.dict',path:'C:/词典/English.dict',exists:true,active:true},{name:'日本語.dict',path:'C:/词典/日本語.dict',exists:true,active:false}],
 acoustic_models:[{name:'English acoustic.zip',path:'C:/模型/English acoustic.zip',exists:true,active:true}],
 wav2vec2_models:[{name:'中文 Wav2Vec2',path:'C:/模型/中文 Wav2Vec2',exists:true,active:true}],
};
const previewWindowRpc = async (method:string, params:Record<string,any>) : Promise<any> => {
 if(method==='tutor.bootstrap') return {...structuredClone(bootstrapFixture),sounds:[{id:'4',name:'标准音_hello'},{id:'9',name:'学习者_hello'}],model:{source:'本地模型',name:'预览演示 · 未连接'},theme:'light',mfa:previewMfa};
 if(method==='mfa.list') return structuredClone(previewMfa);
 if(method==='mfa.save') {previewMfa.presets=previewMfa.presets.map(p=>p.id===params.preset.id?params.preset:p);previewMfa.launcher=params.launcher;return structuredClone(previewMfa);}
 if(method==='anki.settings') return {settings:previewAnki,code:'// 外观预览示例，未导出文件\nconst mode = "both";\nconst selector = ".search-link";'};
 if(method==='anki.save') {previewAnki={...previewAnki,...params};return {settings:previewAnki,message:'预览内暂存；未写入宿主配置。'};}
 const [category,action]=method.split('.');
 if(category in previewResourceLists && action==='list') return {[category==='dictionaries'?'dictionaries':'models']:structuredClone(previewResourceLists[category as keyof typeof previewResourceLists]),theme:'light'};
 throw new Error('此为外观预览，未执行宿主操作。');
};
const previewRouteRpc = new URLSearchParams(location.search).get('demo')==='1' && ['127.0.0.1','localhost'].includes(location.hostname) && !window.pywebview ? previewWindowRpc : undefined;
