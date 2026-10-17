import {expect,test} from '@playwright/test';

test('search focus has no displaced outline; native composition preserves the same focused input',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.getByRole('button',{name:'搜索会话与消息',exact:true}).click();
  const input=page.getByRole('textbox',{name:'搜索会话与消息',exact:true});await input.click();
  expect(await input.evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
  const protocol=await page.context().newCDPSession(page);
  await input.evaluate(el=>{(window as any).searchInputIdentity=el;});
  await protocol.send('Input.imeSetComposition',{text:'ceshi',selectionStart:0,selectionEnd:5});
  await page.waitForTimeout(450);
  expect(await input.evaluate(el=>el===(window as any).searchInputIdentity&&document.activeElement===el)).toBe(true);
  await protocol.send('Input.insertText',{text:'测试'});await expect(input).toHaveValue('测试');
  await page.getByRole('button',{name:'清除搜索',exact:true}).click();await expect(input).toHaveValue('');
  await page.keyboard.press('Control+k');const find=page.getByRole('searchbox',{name:'在会话中查找',exact:true});await expect(find).toBeFocused();
  await find.evaluate(el=>{(window as any).findInputIdentity=el;});
  await protocol.send('Input.imeSetComposition',{text:'nihon',selectionStart:0,selectionEnd:5});
  await protocol.send('Input.insertText',{text:'日本'});await expect(find).toHaveValue('日本');
  expect(await find.evaluate(el=>el===(window as any).findInputIdentity&&document.activeElement===el)).toBe(true);
});

test('search leaves IME keys to the input method and still handles settled Enter/Escape',async({page})=>{
  await page.goto('/?demo=1');await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.getByRole('button',{name:'搜索会话与消息',exact:true}).click();
  const sidebar=page.getByRole('textbox',{name:'搜索会话与消息',exact:true});await sidebar.click();
  const dispatch=async(key:string,options:Record<string,unknown>={})=>page.evaluate(({key,options})=>{
    const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...options});
    document.activeElement!.dispatchEvent(event);return event.defaultPrevented;
  },{key,options});
  expect(await dispatch('k',{ctrlKey:true,isComposing:true})).toBe(false);
  await expect(page.getByRole('search')).toHaveCount(0);
  await page.keyboard.press('Control+k');const search=page.getByLabel('在会话中查找',{exact:true});await expect(search).toBeFocused();
  expect(await search.evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none');
  expect(await search.getAttribute('type')).toBe('text');
  expect(await dispatch('Enter',{isComposing:true})).toBe(false);
  expect(await dispatch('Escape',{isComposing:true})).toBe(false);await expect(search).toBeVisible();
  expect(await dispatch('Enter',{keyCode:229})).toBe(false);
  expect(await dispatch('Escape',{keyCode:229})).toBe(false);await expect(search).toBeFocused();
  await search.fill('fixture');expect(await dispatch('Enter')).toBe(true);
  await page.keyboard.press('Escape');await expect(page.getByRole('search')).toHaveCount(0);
});
