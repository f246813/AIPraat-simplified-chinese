import {expect, test} from '@playwright/test';
import {join} from 'node:path';

test('a delivered report opens in the conversation with values, gaps, inline chart and durable history', async ({page}) => {
  await page.addInitScript(() => {
    const stamp = '2026-10-23T10:00:00Z';
    const session = (id:string,title:string) => ({id,title,created:stamp,updated:stamp,readOnly:false,draft:'',scroll:0});
    const old = session('old','原有会话'); const report = session('report','AI 纠音 · standard → learner');
    const files = [{id:'chart',name:'ai_overlay.png',mime:'image/png',size:100}, {id:'json',name:'ai_report.json',mime:'application/json',size:200}, {id:'grid',name:'ai_errors.TextGrid',mime:'text/plain',size:80}];
    const content = '# AI 纠音报告\n\n**标准音：** standard\n\n**学习者录音：** learner\n\n本次记录 **1 项声学差异**。\n\n## 具体结果\n\n| 音位 | 学习者区间（秒） | 指标 | 标准音 | 学习者 | 结果 |\n|---|---|---|---|---|---|\n| /a/ | 0–0.2 | 基频 | 220 Hz | 200 Hz | 基频偏低 |\n\n## 测量覆盖\n\n- 音位声学对比：部分完成\n\n### 缺测与未评估项\n\n- /a/：没有可用共振峰\n\n## AI 文字解释\n\n本次未生成 AI 文字解释；上面的测量结果和覆盖状态已保存。';
    let delivered = false; const sessions = [old,report];
    (window as any).reportCalls = [];
    (window as any).pywebview = {api:{rpc:async (method:string,p:any) => {
      (window as any).reportCalls.push({method,params:p});
      if (method === 'bootstrap') return {sessions,settings:{api:{},local:{},preferences:{theme:'light',smooth_streaming:false},analysis:{},alignment:{}},tasks:[],host:{name:'offline browser fixture',version:'1',cloudAllowed:false},models:[],providers:[]};
      if (method === 'sessions.create') {const s=session('blank','新会话');sessions.push(s);return s;}
      if (method === 'sessions.get') return {session:sessions.find(s=>s.id===p.sessionId),cursor:0,messages:p.sessionId==='report'?[{id:'report-message',role:'assistant',status:'complete',content,attachments:files,report:{type:'pronunciation',source:'fixture-report.json'}}]:[]};
      if (method === 'events.poll') {if (!delivered) {delivered=true;return {events:[],cursor:0,navigation:{id:'report-delivery',category:'报告',sessionId:'report'}};} return {events:[],cursor:0};}
      if (method === 'attachments.preview') return p.attachmentId==='chart'?{...files[0],dataUrl:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jp1sAAAAASUVORK5CYII='}:{...files.find(f=>f.id===p.attachmentId),text:p.attachmentId==='json'?'{"errors":[]}':'File type = "ooTextFile"'};
      if (method === 'sessions.context') return {sessionId:p.sessionId,estimated:true,inputTokens:100,contextWindow:32768,availableTokens:30000,exclusions:[]};
      return {ok:true};
    }}};
  });
  for (const width of [1040,600,375]) {
    await page.setViewportSize({width,height:850}); await page.goto('/');
    await expect(page.locator('.chat-header h1')).toHaveText('AI 纠音 · standard → learner');
    await expect(page.getByRole('heading',{name:'AI 纠音报告',exact:true})).toBeVisible();
    await expect(page.getByRole('cell',{name:'220 Hz',exact:true})).toBeVisible();
    await expect(page.getByText('/a/：没有可用共振峰',{exact:true})).toBeVisible();
    const chart=page.getByRole('img',{name:'标准音与学习者录音的声学对比叠加图'});
    await chart.scrollIntoViewIfNeeded(); await expect(chart).toBeVisible();
    await expect.poll(()=>chart.evaluate((img:HTMLImageElement)=>img.complete&&img.naturalWidth>0)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.getByRole('button',{name:/ai_errors.TextGrid/}).click();
    await expect(page.getByRole('dialog',{name:'ai_errors.TextGrid'})).toBeVisible();
    await page.getByRole('button',{name:'关闭',exact:true}).click();
    const composer=page.getByRole('textbox',{name:'消息输入'}); await expect(composer).toBeVisible();
    expect(await page.evaluate(()=>(window as any).reportCalls.some((c:any)=>['tasks.submit','settings.test'].includes(c.method)))).toBe(false);
    if (process.env.PI_SCRATCH_DIR) await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,`tutor-report-${width}.png`)});
  }
});
