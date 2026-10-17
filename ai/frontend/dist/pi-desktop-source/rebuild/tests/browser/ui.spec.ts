import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { revealAllHistory } from './reveal';
test('normal browser is fail-closed, never a demo',async({page})=>{await page.goto('/');await expect(page.getByText('需要桌面宿主连接')).toBeVisible({timeout:15000});await expect(page.getByRole('textbox',{name:'消息输入'})).toHaveCount(0);await expect(page.getByText('浏览器测试适配器',{exact:false})).toHaveCount(0);});
test('rich composer IME, newline, drafts, native attachment tags, session CRUD',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/?demo=1');await expect(page.getByRole('button',{name:'展开会话侧栏'})).toBeVisible();
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-1"] .session-select').click();
  await page.getByRole('button',{name:'折叠会话侧栏'}).click();
  const editor=page.getByRole('textbox',{name:'消息输入'});await editor.fill('中文草稿');
  await editor.dispatchEvent('compositionstart');await editor.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,keyCode:229});await expect(page.locator('.message.user')).toHaveCount(0);
  await editor.dispatchEvent('compositionend');await page.waitForTimeout(120);await editor.press('Shift+Enter');await editor.press('x');await expect(page.locator('.message.user')).toHaveCount(0);
  await page.getByRole('button',{name:'添加附件'}).click();await expect(page.getByRole('button',{name:'移除 演示材料.txt'})).toBeVisible();
  await page.getByRole('button',{name:'展开会话侧栏'}).click();await page.getByRole('button',{name:'新建会话',exact:true}).click();await editor.fill('第二会话草稿');
  await page.locator('.session-select').filter({hasText:'新的分析'}).click();await expect(editor).toContainText('中文草稿');await expect(page.getByRole('button',{name:'移除 演示材料.txt'})).toBeVisible();
  await page.getByRole('button',{name:'移除 演示材料.txt'}).click();await expect(page.locator('.composer .attachment-chip')).toHaveCount(0);
  await page.getByRole('button',{name:'会话操作 新的分析',exact:true}).click();await page.getByRole('menuitem',{name:'重命名',exact:true}).click();await page.getByRole('textbox',{name:'会话名称'}).fill('声学项目');await page.getByRole('button',{name:'确认',exact:true}).click();await expect(page.locator('.chat-header h1')).toContainText('声学项目');
  await page.getByRole('button',{name:'会话操作 声学项目',exact:true}).click();await page.getByRole('menuitem',{name:'删除',exact:true}).click();await page.getByRole('button',{name:'确认',exact:true}).click();await expect(page.locator('.session-select').filter({hasText:'声学项目'})).toHaveCount(0);
  await expect(editor).toContainText('第二会话草稿');
  await page.locator('.session-select').filter({hasText:'演示旧记录'}).click();await expect(page.getByText('旧记录只读 · 不恢复或重放旧操作')).toBeVisible();await expect(editor).toHaveCount(0);
  expect(errors).toEqual([]);
});
test('settings categories, key preservation, separate fixture tests and unknown capability',async({page})=>{
  await page.goto('/?demo=1');
  await expect(page.getByRole('button',{name:'搜索 / 浏览历史会话',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'打开设置'}).click();
  await expect(page.getByRole('navigation',{name:'设置分类'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'模型',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'模型',exact:true}).click();
  await expect(page.getByLabel('API Key',{exact:true})).toHaveValue('');
  await page.getByRole('button',{name:'测试文字连接'}).click();await expect(page.locator('.test-result').first()).toContainText('demo-not-verified');await expect(page.locator('.test-result').nth(1)).toContainText('未测试');
  await page.getByRole('button',{name:'验证音频能力'}).click();await expect(page.locator('.test-result').nth(1)).toContainText('demo-not-verified');
  await page.getByRole('textbox',{name:'模型名',exact:true}).fill('changed');await expect(page.locator('.test-result').first()).toContainText('配置已更改');
  await page.getByRole('button',{name:'上下文与输出',exact:true}).click();await expect(page.getByText('此模型能力未知。',{exact:false})).toBeVisible();await page.getByRole('button',{name:'切换手动预算'}).click();await expect(page.getByRole('spinbutton',{name:'上下文 token',exact:true})).toBeVisible();
  for(const name of ['采样与思考','Praat 工具与音频','外观与交互','会话与存储'])await page.getByRole('button',{name,exact:true}).click();
  await page.getByRole('button',{name:'保存更改'}).click();await expect(page.getByText('设置已保存。',{exact:false})).toBeVisible();
  if(process.env.PI_SCRATCH_DIR)await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,'frontend-settings.png')});
});
test('long history math/highlight/diagram, reading position, background stream isolation and cancel',async({page})=>{
  const remote:string[]=[];page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:5178')&&!r.url().startsWith('data:'))remote.push(r.url());});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('.session-select').filter({hasText:'长历史'}).click();
  // The mount window withholds the head of a long history instead of rendering all of it.
  await expect(page.locator('.message')).toHaveCount(60);
  await expect(page.locator('.history-reveal button')).toContainText('显示更早的 61 条消息');
  await expect(page.locator('.katex').first()).toBeAttached();await expect(page.locator('.hljs-built_in').first()).toBeAttached();
  await revealAllHistory(page);await expect(page.locator('.message')).toHaveCount(121);
  const transcript=page.locator('.transcript');await transcript.evaluate(el=>{el.scrollTop=1600;});await page.waitForTimeout(300);const before=await transcript.evaluate(el=>el.scrollTop);
  const topMessage=()=>transcript.evaluate(el=>{const top=el.getBoundingClientRect().top;const rows=Array.from(document.querySelectorAll('[data-message-id]'));const row=rows.find(r=>r.getBoundingClientRect().bottom>top) as HTMLElement|undefined;return row?.dataset.messageId||'';});
  const beforeId=await topMessage();expect(beforeId).not.toBe('');
  await page.locator('.session-select').filter({hasText:'新的分析'}).click();await page.getByRole('textbox',{name:'消息输入'}).fill('隔离测试');await page.getByRole('button',{name:'发送消息'}).click();await expect(page.locator('.message.user')).toHaveCount(1);
  // The reading anchor, not a pixel offset, is what survives the window being rebuilt:
  // the rebuilt window drops the rows above the anchor, so scrollTop is not comparable.
  await page.locator('.session-select').filter({hasText:'长历史'}).click();
  await expect.poll(topMessage).toBe(beforeId);
  await expect(page.locator(`[data-message-id="${beforeId}"]`)).toBeAttached();
  await page.getByRole('button',{name:'回到最新消息'}).click();await expect(page.getByRole('img',{name:'Mermaid 图表'})).toBeVisible({timeout:15000});
  await page.locator('[data-message-id="diagram-fixture"] .process-details > summary').click();
  await page.locator('.tool-card summary').click();await expect(page.locator('.execution-fact')).toContainText('未投递');await expect(page.locator('.activity-body').last()).toContainText('measured');
  await page.locator('.session-select').filter({hasText:'新的分析'}).click();await expect(page.locator('.message.assistant')).toHaveCount(1);await page.getByRole('button',{name:'取消当前任务'}).click();await expect(page.locator('.message.assistant .badge')).toContainText('已取消');
  expect(remote).toEqual([]);expect(errors).toEqual([]);
  if(process.env.PI_SCRATCH_DIR)await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,'frontend-chat.png')});
});
