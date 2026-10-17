import {test, expect} from '@playwright/test';

test.use({launchOptions:{ignoreDefaultArgs:['--hide-scrollbars']}});

test('Pi session sidebar: grouping, sorting, preview and keyboard navigation', async ({page}) => {
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await expect(page.locator('.pi-session-sidebar')).toHaveCount(1);
  await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toBeVisible();
  await page.locator('[data-session-id="demo-1"] .session-select').click();
  for (const [id,title] of [['demo-1','Zulu'],['demo-history','Alpha']]) {
    await page.locator(`[data-session-id="${id}"] .session-more`).click();
    await page.getByRole('menuitem',{name:'重命名',exact:true}).click();
    await page.getByLabel('会话名称',{exact:true}).fill(title);
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await expect(page.locator(`[data-session-id="${id}"]`)).toContainText(title);
  }
  await page.getByRole('button',{name:'会话历史选项',exact:true}).click();
  await page.getByRole('menuitem',{name:'聊天排序方式',exact:true}).hover();
  await page.getByRole('menuitemradio',{name:'按名称',exact:true}).click();
  const order=await page.locator('.session-row').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-session-id')));
  expect(order.indexOf('demo-history')).toBeLessThan(order.indexOf('demo-1'));
  const legacy=page.locator('[data-session-id="legacy:fixture"] .session-select');
  await legacy.hover();
  await expect(page.locator('.sidebar-session-hover-card')).toContainText('这是一条只读旧记录',{timeout:5000});
  await expect(page.locator('.chat-header h1')).toContainText('Zulu');
  await page.locator('.chat-header h1').hover(); await page.waitForTimeout(50);
  await legacy.hover(); await page.waitForTimeout(250);
  await expect(page.locator('.sidebar-session-hover-card')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.sidebar-session-hover-card')).toHaveCount(0);
  await legacy.focus(); await page.keyboard.press('Home');
  await expect(page.locator('.session-select').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.session-select').nth(1)).toBeFocused();
  await page.getByRole('button',{name:'会话历史选项',exact:true}).click();
  await page.getByRole('menuitem',{name:'聊天排序方式',exact:true}).hover();
  await page.getByRole('menuitemradio',{name:'最近更新',exact:true}).click();
  await page.getByRole('button',{name:'折叠 今天',exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(0);
  await page.getByRole('button',{name:'展开 今天',exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(4);
  await legacy.hover();
  await expect(page.locator('.sidebar-session-hover-card')).toBeVisible();
  await page.getByRole('button',{name:'打开会话',exact:true}).click();
  await expect(page.locator('.chat-header h1')).toContainText('演示旧记录（只读）');
  await expect(page.locator('.sidebar-session-hover-card')).toHaveCount(0);
  await page.locator('.chat-header h1').click(); await page.keyboard.press('Control+b');
  await expect(page.getByRole('button',{name:'展开会话侧栏'})).toBeVisible();
  await page.keyboard.press('Control+b');
  await page.getByRole('button',{name:'搜索会话与消息',exact:true}).click();
  await page.getByRole('textbox',{name:'搜索会话与消息'}).focus(); await page.keyboard.press('Control+b');
  await expect(page.getByRole('button',{name:'折叠会话侧栏'})).toBeVisible();
});

test('Pi multiple selection: modifier clicks preserve active chat and allow readonly deletion', async ({page}) => {
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.locator('[data-session-id="demo-1"] .session-select').click({modifiers:['Control']});
  await page.locator('[data-session-id="demo-history"] .session-select').click({modifiers:['Control']});
  await expect(page.locator('.sidebar-selection-bar')).toContainText('已选 2 个会话');
  await expect(page.locator('.chat-header h1')).toContainText('新会话');
  await page.getByRole('button',{name:'删除所选会话',exact:true}).click();
  await expect(page.getByRole('button',{name:'确认删除 2 个会话',exact:true})).toBeVisible();
  await page.locator('[data-session-id="demo-history"] .session-select').click({modifiers:['Control']});
  await expect(page.getByRole('button',{name:'删除所选会话',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'确认删除 1 个会话',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'取消多选',exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(4);
  await page.locator('[data-session-id="demo-history"] .session-select').click({modifiers:['Control']});
  await page.locator('[data-session-id="legacy:fixture"] .session-select').click({modifiers:['Shift']});
  await expect(page.getByRole('button',{name:'删除所选会话',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'取消多选',exact:true}).click();
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('.chat-header h1')).toContainText('长历史与渲染夹具');
  await page.locator('[data-session-id="demo-1"] .session-select').click({modifiers:['Control']});
  await page.locator('[data-session-id="demo-history"] .session-select').click({modifiers:['Control']});
  await page.getByRole('button',{name:'删除所选会话',exact:true}).click();
  await page.waitForTimeout(3300);
  await expect(page.getByRole('button',{name:'删除所选会话',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'删除所选会话',exact:true}).click();
  await page.getByRole('button',{name:'确认删除 2 个会话',exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(2);
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toBeVisible();
  await expect(page.locator('.chat-header h1')).toContainText('新会话');
});

test('native sidebar scrollbar has a wide pointer lane and drags independently of sidebar resizing', async ({page}) => {
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  const draft=page.getByRole('textbox',{name:'消息输入'});
  for (let count=5;count<=45;count++) {
    // A draft makes the current session non-blank, so the blank-session guard creates the next one.
    await draft.fill(`草稿 ${count}`);
    await page.getByRole('button',{name:'新建会话',exact:true}).click();
    await expect(page.locator('.session-row')).toHaveCount(count);
  }
  const list=page.locator('.session-list');
  const sample=await list.evaluate(el=>({height:el.clientHeight,total:el.scrollHeight,lane:(el as HTMLElement).offsetWidth-el.clientWidth,width:getComputedStyle(el,'::-webkit-scrollbar').width,side:el.closest('.sidebar')!.getBoundingClientRect().width}));
  expect(sample.width).toBe('14px'); expect(sample.lane).toBe(14);
  const rect=(await list.boundingBox())!;
  // Hit the previously unclickable outer portion of the widened native lane.
  const x=rect.x+rect.width-11, y=rect.y+sample.height*sample.height/sample.total/2;
  await page.mouse.move(x,y); await page.mouse.down(); await page.mouse.move(x,y+100,{steps:8}); await page.mouse.up();
  expect(await list.evaluate(el=>el.scrollTop)).toBeGreaterThan(20);
  expect(await page.locator('.sidebar').evaluate(el=>el.getBoundingClientRect().width)).toBe(sample.side);
  const edge=await list.evaluate(el=>el.closest('.sidebar')!.getBoundingClientRect().right-el.getBoundingClientRect().right);
  expect(edge).toBe(1);
  await page.getByRole('button',{name:'搜索会话与消息',exact:true}).click();
  await page.getByRole('textbox',{name:'搜索会话与消息'}).fill('新的分析');
  await expect(page.locator('.session-row')).toHaveCount(1);
  expect(await list.evaluate(el=>el.scrollHeight)).toBe(await list.evaluate(el=>el.clientHeight));
});
