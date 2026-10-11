import {expect,test} from '@playwright/test';
import {readFileSync} from 'node:fs';

test('Anki exporter, editable entry and audio test share settings',async({page})=>{
  await page.addInitScript(()=>{
    let settings={media_dir:'',ffmpeg:'',fallback:true,audio_field:'',start_field:'',end_field:'',mode:'button',magnifier_selector:'.Search'};
    (window as any).calls=[];
    (window as any).pywebview={api:{rpc:async(method:string,params:any)=>{
      (window as any).calls.push({method,params});
      if(method==='anki.settings') return {settings,code:'const AIPRAAT_ENTRY = "button";'};
      if(method==='anki.save'){settings=params;return {settings,code:'const AIPRAAT_ENTRY = "'+settings.mode+'";'};}
      return {message:method==='anki.open'?'已打开语图':'已导出插件'};
    }}};
  });
  await page.setViewportSize({width:860,height:740});
  await page.goto('/?window=anki');
  expect((await page.getByRole('radio').first().boundingBox())!.width).toBeLessThan(24);
  await page.getByLabel('复用原放大镜',{exact:true}).check();
  await page.getByRole('button',{name:'导出 Anki 插件'}).click();
  await expect(page.getByRole('status')).toHaveText('已导出插件');
  await page.getByLabel('测试音频路径').fill('C:/卡片/接する.M4A');
  await page.getByRole('button',{name:'打开到语图视窗'}).click();
  await expect(page.getByRole('status')).toHaveText('已打开语图');
  const calls=await page.evaluate(()=>(window as any).calls);
  expect(calls.find((c:any)=>c.method==='anki.save').params.mode).toBe('magnifier');
  expect(calls.find((c:any)=>c.method==='anki.open').params.audio).toBe('C:/卡片/接する.M4A');
  await page.locator('main').evaluate(element=>element.scrollTop=0);
  await page.screenshot({path:'../../installer/verification/anki-integration-860.png',fullPage:true});
  await page.setViewportSize({width:660,height:540});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('existing JLPT magnifier dispatches once and suppresses GoldenDict',async({page})=>{
  await page.setContent('<html class="win"><body><a class="Search" href="goldendict://test"><span>🔍</span></a></body></html>');
  await page.evaluate(()=>{
    (window as any).events=[];
    (window as any).pycmd=(message:string)=>(window as any).events.push(message);
    document.querySelector('.Search')!.addEventListener('click',()=>{(window as any).events.push('goldendict');});
  });
  await page.addScriptTag({content:readFileSync('../anki_addon/entry.js','utf8')});
  await page.evaluate(()=>{(window as any).aipraatAttachMagnifier('.Search');(window as any).aipraatAttachMagnifier('.Search');});
  await page.locator('.Search span').click();
  expect(await page.evaluate(()=>(window as any).events)).toEqual(['aipraat:open']);
});
