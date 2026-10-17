import {expect,test} from '@playwright/test';
import {join} from 'node:path';

test('production sidebar brand row has compact controls and right-side disclosure arrows',async({page})=>{
  await page.goto('/?demo=1');
  await expect(page.getByRole('button',{name:'展开会话侧栏'}).locator('.lucide-panel-left-open')).toBeVisible();
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  const header=page.locator('.sidebar-brand-row');
  await expect(header).toHaveText('Praat');
  await expect(header.getByRole('button',{name:'会话历史选项'}).locator('.lucide-chevron-down')).toBeVisible();
  await expect(header.getByRole('button',{name:'折叠会话侧栏'}).locator('.lucide-panel-left-close')).toBeVisible();
  await expect(page.getByRole('textbox',{name:'搜索会话与消息'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'新建会话',exact:true}).locator('.lucide-square-pen')).toBeVisible();
  const metrics=await header.evaluate(el=>{
    const style=getComputedStyle(el),brand=el.querySelector('strong')!;
    const actions=el.querySelector('.sidebar-header-actions')!;
    return {padding:[style.paddingTop,style.paddingRight,style.paddingBottom,style.paddingLeft],font:getComputedStyle(brand).fontSize,gap:getComputedStyle(actions).gap,buttons:[...actions.children].map(button=>({width:button.getBoundingClientRect().width,height:button.getBoundingClientRect().height}))};
  });
  expect(metrics).toEqual({padding:['18px','16px','14px','16px'],font:'16px',gap:'8px',buttons:[{width:28,height:28},{width:28,height:28}]});
  for(const selector of ['.history-section-toggle','.sidebar-origin-header','.sidebar-time-group-header']){
    const ordered=await page.locator(selector).first().evaluate(el=>{
      const name=el.querySelector('.history-section-name,.sidebar-origin-name,.sidebar-group-name')!.getBoundingClientRect();
      const arrow=el.querySelector(':scope>svg')!.getBoundingClientRect();
      const count=el.querySelector('.history-section-count,.sidebar-group-count')!.getBoundingClientRect();
      return name.right<=arrow.left&&arrow.right<=count.left;
    });
    expect(ordered).toBe(true);
  }
  // The minimum persisted width still has no overlap between branding/actions.
  await page.getByRole('separator',{name:'调整会话侧栏宽度'}).press('Home');
  const fits=()=>header.evaluate(el=>el.querySelector('.brand')!.getBoundingClientRect().right<=el.querySelector('.sidebar-header-actions')!.getBoundingClientRect().left);
  await expect.poll(fits).toBe(true);
});

test('search opens on demand, resets selection, and Escape restores the trigger without filtering hidden results',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-history"] .session-select').click({modifiers:['Control']});
  await expect(page.locator('.sidebar-selection-bar')).toContainText('已选 1 个会话');
  const trigger=page.getByRole('button',{name:'搜索会话与消息',exact:true});
  await trigger.click();
  await expect(page.locator('.sidebar-selection-bar')).toHaveCount(0);
  const input=page.getByRole('textbox',{name:'搜索会话与消息',exact:true});
  await expect(input).toBeFocused();await expect(trigger).toHaveAttribute('aria-expanded','true');
  await input.fill('仅离线渲染测试');await expect(page.locator('.session-row')).toHaveCount(1);
  await input.press('Escape');
  await expect(input).toHaveCount(0);await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded','false');await expect(page.locator('.session-row')).toHaveCount(4);
  await trigger.click();await expect(input).toHaveValue('');
  await input.fill('无匹配的查询');await trigger.click();
  await expect(input).toHaveCount(0);await expect(page.locator('.session-row')).toHaveCount(4);
});

test('default section collapse does not hide pinned or custom sessions and keeps creation/menu actions reachable',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-1"] .session-more').click();
  await page.getByRole('menuitem',{name:'置顶',exact:true}).click();
  await page.getByRole('button',{name:'新建分区',exact:true}).click();
  await page.getByLabel('分区名称',{exact:true}).fill('侧栏验证');
  await page.getByRole('button',{name:'保存分区',exact:true}).click();
  await page.locator('[data-session-id="demo-history"] .session-more').click();
  await page.getByRole('menuitem',{name:'分区',exact:true}).click();
  await page.getByRole('menuitem',{name:'侧栏验证',exact:true}).click();
  await page.getByRole('button',{name:'折叠分区 会话',exact:true}).click();
  await expect(page.locator('[data-session-id="demo-1"]')).toBeVisible();
  await expect(page.locator('[data-session-id="demo-history"]')).toBeVisible();
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'新建分区',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'分区操作 会话',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'归档分区',exact:true})).toBeEnabled();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'展开分区 会话',exact:true}).click();
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toBeVisible();
});

test('sidebar bottom entries are compact left-aligned transparent gray rows, not white buttons',async({page})=>{
  await page.setViewportSize({width:1080,height:850});
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  const bottom=page.locator('.sidebar-bottom');
  for(const selector of ['.task-entry','.settings-entry']){
    const button=bottom.locator(selector);
    await expect(button).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    await expect(button).toHaveCSS('border-top-width','0px');
    await expect(button).toHaveCSS('justify-content','flex-start');
    await expect(button).toHaveCSS('height','30px');
    await expect(button).toHaveCSS('color','rgb(110, 110, 115)');
  }
});

test('collapsed sidebar hides the redundant brand mark and restores it when expanded',async({page})=>{
  await page.goto('/?demo=1');
  const brand=page.locator('.sidebar-brand-row .brand');
  await expect(brand.locator('.lucide-audio-lines')).toHaveCount(0);
  await page.screenshot({path:process.env.PI_SCRATCH_DIR?join(process.env.PI_SCRATCH_DIR,'sidebar-collapsed.png'):test.info().outputPath('sidebar-collapsed.png')});
  await expect(page.getByRole('button',{name:'展开会话侧栏'}).locator('.lucide-panel-left-open')).toBeVisible();
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await expect(brand.locator('.lucide-audio-lines')).toHaveCount(1);
  await expect(brand).toContainText('Praat');
});

test('new session reuses the blank current one and only creates after a draft exists',async({page})=>{
  await page.setViewportSize({width:1080,height:850});
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  const rows=page.locator('.session-row');
  const before=await rows.count();
  // Clicking on an untouched blank session must not pile up another empty row.
  await page.getByRole('button',{name:'新建会话',exact:true}).click();
  await expect(rows).toHaveCount(before);
  await expect(page.locator('.chat-header h1')).toHaveText('新会话');
  // Once the session carries a draft it is no longer blank, so the next click creates.
  await page.getByRole('textbox',{name:'消息输入'}).fill('准备开始');
  await page.getByRole('button',{name:'新建会话',exact:true}).click();
  await expect(rows).toHaveCount(before+1);
});
