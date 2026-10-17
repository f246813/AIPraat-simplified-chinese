import {expect,test} from '@playwright/test';

test('opening and scrolling a body-search conversation does not rebuild the sidebar results',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.getByRole('button',{name:'搜索会话与消息',exact:true}).click();
  await page.getByRole('textbox',{name:'搜索会话与消息'}).fill('仅离线渲染测试');
  await expect(page.locator('.session-row')).toHaveCount(1);
  await expect(page.locator('.search-status')).toHaveCount(0);
  await page.evaluate(()=>{
    (window as any).matchedRow=document.querySelector('.session-row');
    (window as any).searchReloads=0;
    new MutationObserver(records=>{
      for(const record of records)for(const node of record.addedNodes){
        if(node instanceof Element&&(node.matches('.search-status')||node.querySelector('.search-status')))(window as any).searchReloads++;
      }
    }).observe(document.querySelector('.session-list')!,{childList:true,subtree:true});
  });
  await page.locator('[data-session-id="demo-history"] .session-select').click();
  await expect(page.locator('[data-message-id="diagram-fixture"]')).toBeAttached();
  const transcript=page.getByLabel('会话消息',{exact:true});
  await transcript.hover();await page.mouse.wheel(0,-420);
  await expect.poll(()=>transcript.evaluate(e=>e.scrollHeight-e.clientHeight-e.scrollTop)).toBeGreaterThan(100);
  await page.mouse.wheel(0,180);
  await page.getByRole('button',{name:'刷新当前会话',exact:true}).click();
  await page.waitForTimeout(650); // Beyond the search and view-write debounce windows.
  expect(await page.evaluate(()=>(window as any).matchedRow===document.querySelector('.session-row'))).toBe(true);
  expect(await page.evaluate(()=>(window as any).searchReloads)).toBe(0);
  await expect(page.locator('.session-row')).toHaveCount(1);
  await expect(page.getByRole('textbox',{name:'搜索会话与消息'})).toHaveValue('仅离线渲染测试');
});
