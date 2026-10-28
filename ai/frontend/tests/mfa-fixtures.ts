import type { MfaData, MfaEnvironment, MfaStatus } from '../src/MfaPresetEditor';
import type { Generation } from '../src/PronunciationForm';
export const readyStatus: MfaStatus = {g2p:'static_ok',dictionary:'static_ok',acoustic:'static_ok',compatibility:'unknown',issues:[],can_generate:true};
export const environmentFixture: MfaEnvironment = {
  schema:'mfa-alignment-probe-v1', ready:false,
  preset:{id:'en-US',name:'英语（美式）',language:'en',variant:'US',dictionary:'d',acoustic:'a'},
  launcher:{executable:'C:/MFA/mfa.exe',conda_executable:'',conda_environment:''},
  summary:'对齐环境不完整（2 项）：libsndfile 音频读写 — cannot load library',
  status:readyStatus,
  resources:{g2p:{id:'g',name:'English G2P.zip',path:'C:/模型/English G2P.zip',exists:true},
    dictionary:{id:'d',name:'English.dict',path:'C:/词典/English.dict',exists:true},
    acoustic:{id:'a',name:'English acoustic.zip',path:'C:/模型/English acoustic.zip',exists:true}},
  environment:{schema:'mfa-alignment-probe-v1',ready:false,summary:'对齐环境不完整（2 项）：libsndfile 音频读写 — cannot load library',
    launcher:'C:/MFA/mfa.exe',environment:'MFA',version:'3.4.2',python:'C:/MFA/python.exe',blocking:['soundfile','openfst'],
    checks:[{id:'launcher',label:'MFA 启动方式',status:'ok',detail:'C:/MFA/mfa.exe · 3.4.2',repair:''},
      {id:'soundfile',label:'libsndfile 音频读写',status:'error',detail:'cannot load library',repair:'改用 conda run：直接调用 Scripts\\mfa.exe 不会激活环境。'},
      {id:'openfst',label:'OpenFST 工具',status:'error',detail:'fstcompile 不在 PATH',repair:'用 conda run 启动以带上 Library\\bin。'}]},
  blocking:['libsndfile 音频读写：cannot load library','OpenFST 工具：fstcompile 不在 PATH','MFA 启动程序未找到或 Conda 配置不完整'],
  notes:['检测项目：MFA 启动、Python 依赖与资源兼容性'],
};
export const mfaFixture: MfaData = {
  theme:'system',default_preset:'',launcher:{executable:'C:/MFA/mfa.exe',conda_executable:'',conda_environment:''},
  resources:{g2p:[{id:'g',name:'English G2P.zip',path:'C:/模型/English G2P.zip',exists:true,used_by:['en-US']}],dictionary:[{id:'d',name:'English.dict',path:'C:/词典/English.dict',exists:true,used_by:['en-US']}],acoustic:[{id:'a',name:'English acoustic.zip',path:'C:/模型/English acoustic.zip',exists:true,used_by:['en-US']}]},
  presets:[{id:'en-GB',name:'英语（英式）',language:'en',variant:'GB',rule_id:null,g2p:'',dictionary:'',acoustic:'',phone_set:'arpabet',phone_map:{},status:{g2p:'missing',dictionary:'missing',acoustic:'missing',compatibility:'unknown',issues:['请添加英式 G2P 模型。'],can_generate:false}},
    {id:'en-US',name:'英语（美式）',language:'en',variant:'US',rule_id:'en-US-general-v1',g2p:'g',dictionary:'d',acoustic:'a',phone_set:'arpabet',phone_map:{},status:readyStatus}],
};
export const bootstrapFixture = {sounds:[{id:'4',name:'标准音'},{id:'9',name:'学习者'}],profiles:[{id:'en-US-general-v1',language:'en',name:'英语（美式）'},{id:'ja-JP-standard-v1',language:'ja',name:'日语（标准语）'}],model:{source:'本地模型',name:'local-fixture'},theme:'system',mfa:mfaFixture};
export const generationFixture: Generation = {id:'generation-1',preset_id:'en-US',transcript:'hello hello',words:[{word:'hello',candidates:[{raw:['HH','AH0','L','OW1'],ipa:['h','ə','l','ˈoʊ'],source:'MFA G2P'}]}],sequence:['hello','hello'],source:'MFA G2P · English G2P.zip'};
