import {expect,test} from '@playwright/test';

async function open(page:any){
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
}

test('right-clicking the smallest time group offers exactly the requested actions',async({page})=>{
  await open(page);
  await page.getByRole('button',{name:'折叠 今天',exact:true}).click({button:'right'});
  await expect(page.getByRole('menu')).toBeVisible();
  expect(await page.getByRole('menuitem').allTextContents()).toEqual(['仅删除分区','删除分区内所有内容','全部归档','删除分区及其内容']);
  await page.getByRole('menuitem',{name:'仅删除分区',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('保留');
  await page.getByRole('button',{name:'取消',exact:true}).click();
  await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'折叠 今天',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:'仅删除分区',exact:true}).click();
  await page.getByRole('button',{name:'确认仅删除分区',exact:true}).click();
  await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toHaveCount(0);
  await expect(page.locator('.session-row')).toHaveCount(4);
  await page.getByRole('button',{name:'打开设置'}).click();
  await page.getByRole('button',{name:'刷新宿主记录'}).click();
  await page.getByRole('button',{name:'返回聊天'}).click();
  await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toHaveCount(0);
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('.transcript .message').first()).toBeVisible();
});

for(const action of ['删除分区及其内容','删除分区内所有内容'])test(`${action} includes readonly history and leaves other sections intact`,async({page})=>{
  await open(page);
  await page.getByRole('button',{name:'新建分区',exact:true}).click();
  await page.getByRole('textbox',{name:'分区名称'}).fill('保留区');
  await page.getByRole('button',{name:'保存分区',exact:true}).click();
  await page.locator('[data-session-id="demo-1"]').click({button:'right'});
  await page.getByRole('menuitem',{name:'分区',exact:true}).click();
  await page.getByRole('menuitem',{name:'保留区',exact:true}).click();
  await page.getByRole('button',{name:'折叠 今天',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:action,exact:true}).click();
  if(action==='删除分区内所有内容'){
    await expect(page.getByRole('dialog')).toContainText('分区会保留');
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await expect(page.locator('.session-row')).toHaveCount(4);
    await page.getByRole('button',{name:'折叠 今天',exact:true}).click({button:'right'});
    await page.getByRole('menuitem',{name:action,exact:true}).click();
  }
  await page.getByRole('button',{name:`确认${action}`,exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(1);
  await expect(page.locator('[data-session-id="demo-1"]')).toBeVisible();
  await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toHaveCount(action==='删除分区内所有内容'?1:0);
  await expect(page.locator('.chat-header h1')).toHaveText('新的分析');
  if(action==='删除分区内所有内容'){
    await page.getByRole('button',{name:'打开设置'}).click();
    await page.getByRole('button',{name:'刷新宿主记录'}).click();
    await page.getByRole('button',{name:'返回聊天'}).click();
    await expect(page.locator('.session-row')).toHaveCount(1);
    await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toBeVisible();
    await expect(page.locator('.sidebar-session-group-body .history-section-empty')).toHaveText('暂无会话');
    await page.getByRole('textbox',{name:'消息输入'}).fill('归档后继续');
    await page.getByRole('button',{name:'新建会话',exact:true}).click();
    await expect(page.getByRole('button',{name:'折叠 今天',exact:true})).toBeVisible();
    await expect(page.locator('.session-row')).toHaveCount(2);
  }
});

test('archive all group contents can be restored through the existing archive page',async({page})=>{
  await open(page);
  await page.getByRole('button',{name:'折叠 今天',exact:true}).click({button:'right'});
  await page.getByRole('menuitem',{name:'全部归档',exact:true}).click();
  await page.getByRole('button',{name:'确认全部归档',exact:true}).click();
  await expect(page.locator('.session-row')).toHaveCount(0);
  await page.getByRole('button',{name:'打开设置'}).click();
  await page.getByRole('button',{name:'已归档会话',exact:true}).click();
  await expect(page.locator('.archived-session-row')).toHaveCount(4);
  await page.getByRole('button',{name:'取消归档 演示旧记录（只读）',exact:true}).click();
  await page.getByRole('button',{name:'返回聊天'}).click();
  await expect(page.locator('[data-session-id="legacy:fixture"]')).toBeVisible();
});

test('cleared empty group remains until explicitly removed',async({page})=>{
  await open(page);
  const header=page.getByRole('button',{name:'折叠 今天',exact:true});
  await header.click({button:'right'});
  await page.getByRole('menuitem',{name:'删除分区内所有内容',exact:true}).click();
  await page.getByRole('button',{name:'确认删除分区内所有内容',exact:true}).click();
  await expect(header).toBeVisible();
  await expect(page.locator('.session-row')).toHaveCount(0);
  await expect(page.locator('.sidebar-session-group-body .history-section-empty')).toHaveText('暂无会话');
  await header.click({button:'right'});
  await page.getByRole('menuitem',{name:'仅删除分区',exact:true}).click();
  await page.getByRole('button',{name:'确认仅删除分区',exact:true}).click();
  await expect(header).toHaveCount(0);
});
