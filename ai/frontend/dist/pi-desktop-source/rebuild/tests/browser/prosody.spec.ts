import {expect,test} from '@playwright/test';
import {join} from 'node:path';
for(const width of [1040,600]) test(`independent pitch/level switches, baseline confirmation and runtime repair entry ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.addInitScript(()=>{
    (window as any).tutorCalls=[];
    (window as any).pywebview={api:{rpc:async(method:string,params:Record<string,unknown>)=>{
      (window as any).tutorCalls.push({method,params});
      if(method==='tutor.bootstrap')return {sounds:[{id:'4',name:'标准音'},{id:'9',name:'学习者'}],profiles:[{id:'ja-JP-standard-v1',language:'ja',name:'日语（标准语）'}],model:{source:'本地模型',name:'fixture'},theme:'light',normalization:{version:'normalization-v1',formants:false,pitch:false,loudness:false,inventories:{'ja-JP-standard-v1':['a','i','ɯ','e','o']}}};
      if(method==='tutor.configure_runtime')return {opened:true,message:'已打开路径配置，请选择配置运行库'};
      return {accepted:true};
    }}};
  });
  await page.goto('/?window=pronunciation');
  await page.getByRole('radio',{name:/音位判据/}).check();
  await page.getByRole('checkbox',{name:/相对音高（半音/}).check();
  await page.getByRole('checkbox',{name:/整段恒定响度/}).check();
  await expect(page.getByText(/音高证据不足或未确认/)).toBeVisible();
  await page.getByRole('checkbox',{name:/已检查 F0/}).check();
  await expect(page.getByText(/预计使用本句相对半音/)).toBeVisible();
  await expect(page.getByText(/任一侧较短时双方统一/)).toBeVisible();
  await page.getByRole('button',{name:'路径配置 → 配置运行库…'}).click();
  await expect(page.getByText('已打开路径配置，请选择配置运行库')).toBeVisible();
  const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  expect(bounds.scroll).toBeLessThanOrEqual(bounds.width+1);
  if(process.env.PI_SCRATCH_DIR)await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,`prosody-${width}.png`),fullPage:true});
  await page.getByRole('textbox',{name:/目标音位/}).fill('a');
  await page.getByRole('button',{name:'开始分析'}).click();
  const calls=await page.evaluate(()=>(window as any).tutorCalls);
  expect(calls.filter((c:any)=>c.method==='tutor.configure_runtime')).toHaveLength(1);
  expect(calls.find((c:any)=>c.method==='tutor.submit').params.normalization).toMatchObject({pitch:true,loudness:true,pitch_confirmed:true,formants:false});
  expect(calls.some((c:any)=>c.method.includes('install'))).toBe(false);
});
