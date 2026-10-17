import { expect, test } from '@playwright/test';
import { join } from 'node:path';

async function chat(page: import('@playwright/test').Page) {
  await page.emulateMedia({colorScheme:'light'});
  await page.goto('/?demo=1');
  await page.getByRole('button', {name:'新建会话', exact:true}).click();
  await expect(page.locator('.tiptap[contenteditable=true]')).toBeVisible();
}

test('final chrome and stable composer expansion preserve saved sizes and rich editor', async ({page}) => {
  await page.setViewportSize({width:1440,height:900});
  await chat(page);
  const widthHandle=page.locator('.chat-width-handle-right');
  // Fixture has a pre-existing 850px preference: do not migrate saved sizes.
  await expect(widthHandle).toHaveAttribute('aria-valuenow','850');
  await widthHandle.focus(); await page.keyboard.press('Home');
  await expect(widthHandle).toHaveAttribute('aria-valuenow','720');
  const editor=page.locator('.tiptap[contenteditable=true]');
  const stack=page.locator('.composer-stack');
  const width=async()=>Math.round((await stack.boundingBox())!.width);
  expect(await width()).toBe(720);
  await editor.fill('第一行'); await editor.press('End');
  for (const text of ['第二行','第三行','第四行']) {await editor.press('Shift+Enter'); await page.keyboard.insertText(text);}
  await expect(page.locator('.composer-zone')).toHaveClass(/composer-wide/);
  expect(await width()).toBe(816);
  await page.waitForTimeout(350); expect(await width()).toBe(816);
  await editor.fill('短内容');
  await expect(page.locator('.composer-zone')).not.toHaveClass(/composer-wide/);
  expect(await width()).toBe(720);
  await editor.fill('自动折行内容与 English words '.repeat(45));
  await expect(page.locator('.composer-zone')).toHaveClass(/composer-wide/);
  await page.getByRole('button',{name:'展开会话侧栏',exact:true}).click();
  await page.setViewportSize({width:1080,height:850});
  await expect.poll(width).toBeLessThanOrEqual(760);
  await editor.fill('删短');
  await expect(page.locator('.composer-zone')).not.toHaveClass(/composer-wide/);
  await expect(widthHandle).toHaveAttribute('aria-valuenow','720');
  await expect(page.locator('.chat-header p')).toHaveCount(0);
  await expect(page.locator('.chat-header h1')).toHaveCSS('font-size','14px');
  await expect(page.locator('.chat-header h1')).toHaveCSS('font-weight','600');
  await expect(page.locator('.send-button')).toHaveCSS('background-color','rgb(40, 105, 223)');
  await expect(page.locator('.composer')).toHaveCSS('border-top-width','0px');
  // No simulated wallpaper in production styles; desktop composition has separate host coverage.
  await expect(page.locator('body')).toHaveCSS('background-image','none');
  await expect(page.locator('body')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
  await expect(page.locator('.main-surface')).toHaveCSS('background-color','rgba(255, 255, 255, 0.95)');
});

test('conversation title resists selection and the expanded composer reaches 7/12 of the pane',async({page})=>{
  await page.setViewportSize({width:1920,height:1080});
  await chat(page);
  // The abandoned hairline is gone; the title itself is the native drag target.
  await expect(page.locator('.chat-header')).toHaveCSS('user-select','none');
  await expect(page.locator('.chat-header')).toHaveCSS('box-shadow','none');
  // Four visual lines take the composer to its widest form.
  const editor=page.locator('.tiptap[contenteditable=true]');
  await editor.fill('第一行'); await editor.press('End');
  for(const text of ['第二行','第三行','第四行']){await editor.press('Shift+Enter');await page.keyboard.insertText(text);}
  await expect(page.locator('.composer-zone')).toHaveClass(/composer-wide/);
  const ratio=await page.evaluate(()=>{
    const main=document.querySelector('.main-surface')!.getBoundingClientRect();
    const stack=document.querySelector('.composer-stack')!.getBoundingClientRect();
    return stack.width/main.width;
  });
  expect(ratio).toBeGreaterThan(0.55);
  expect(ratio).toBeLessThan(0.61);
  if(process.env.PI_SCRATCH_DIR)await page.screenshot({path:join(process.env.PI_SCRATCH_DIR,'composer-wide-1920.png')});
});

test('the resting composer also follows the pane instead of a fixed 720 band',async({page})=>{
  await chat(page);
  await page.locator('.tiptap[contenteditable=true]').fill('短');
  const measure=()=>page.evaluate(()=>{
    const main=document.querySelector('.main-surface')!.getBoundingClientRect();
    const stack=document.querySelector('.composer-stack')!.getBoundingClientRect();
    return {main:main.width,stack:stack.width,wide:document.querySelector('.composer-zone')!.classList.contains('composer-wide')};
  });
  await page.setViewportSize({width:1920,height:1080});
  const wide=await measure();
  expect(wide.wide).toBe(false);
  // Grew well past the old fixed band because the pane is wide.
  expect(wide.stack).toBeGreaterThan(720);
  expect(wide.stack/wide.main).toBeGreaterThan(0.45);
  expect(wide.stack/wide.main).toBeLessThan(0.55);
  // Narrowing the pane narrows the resting composer with it.
  await page.setViewportSize({width:1000,height:800});
  const narrow=await measure();
  expect(narrow.stack).toBeLessThan(wide.stack);
});

test('dragging the conversation header asks the host to move the window instead of selecting the title',async({page})=>{
  await chat(page);
  await page.evaluate(()=>{(window as any).__dshWindowDrags=0;});
  await page.locator('.chat-header h1').click();
  await expect.poll(()=>page.evaluate(()=>(window as any).__dshWindowDrags||0)).toBeGreaterThan(0);
  // Header buttons keep their own click semantics.
  await page.getByRole('button',{name:'刷新当前会话'}).click();
  expect(await page.evaluate(()=>(window as any).__dshWindowDrags)).toBe(1);
  await expect(page.locator('.chat-title-text')).toHaveCSS('user-select','none');
});

test('real Markdown math remains offline, italic and unboxed; quote/code/table rendering survives',async({page})=>{
  await chat(page);
  const text='$$\n\\mathrm{VOT} = t_{\\text{起声}} - t_{\\text{爆破释放}}\n$$\n\n行内 $x=2$。\n\n> 普通引用\n\n```text\nx=2\n```\n\n| A | B |\n| - | - |\n| 1 | 2 |';
  // Mount the actual safe Markdown component with raw model output; do not inject
  // HTML or pass LaTeX through the user's rich-editor Markdown escaping path.
  await page.evaluate(async text=>{
    const componentPath='/src/Markdown.tsx',reactPath='/node_modules/.vite/deps/react.js',domPath='/node_modules/.vite/deps/react-dom_client.js';
    const [{Markdown},reactModule,domModule]=await Promise.all([import(componentPath),import(reactPath),import(domPath)]);
    const React=reactModule.default||reactModule,createRoot=domModule.createRoot||domModule.default.createRoot;
    const node=document.createElement('div');node.id='math-design-test';document.querySelector('.transcript-content')!.append(node);
    createRoot(node).render(React.createElement(Markdown,{text,rpc:async()=>{},onError:(e:unknown)=>{throw e;}}));
  },text);
  const message=page.locator('#math-design-test');
  await expect(message.locator('.katex-display')).toHaveCount(1);
  await expect(message.locator('.katex').first()).toHaveCSS('font-weight','500');
  await expect(message.locator('.katex .mathrm').first()).toHaveCSS('font-style','italic');
  await expect(message.locator('.katex-display')).toHaveCSS('border-left-width','2px');
  await expect(message.locator('.katex-display')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
  await expect(message.locator('blockquote')).toContainText('普通引用');
  await expect(message.locator('pre')).toContainText('x=2');
  await expect(message.locator('table')).toContainText('A');
});

test('opaque window base keeps the 14px/18px header and composer blur fades after the transparency rollback',async({page})=>{
  await page.setViewportSize({width:1080,height:850});
  await chat(page);
  // The desktop see-through requirement was dropped; the window paints an opaque base.
  await expect(page.locator('#root')).toHaveCSS('background-color','rgb(255, 255, 255)');
  await expect(page.locator('body')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
  // The welcome mark keeps its own colour instead of the old grayscale filter.
  await expect(page.locator('.welcome-mark img')).toHaveCSS('filter','none');
  const header=await page.locator('.chat-header').evaluate(el=>{const s=getComputedStyle(el,'::after');return {content:s.content,height:s.height,filter:s.backdropFilter,pointer:s.pointerEvents,mask:s.maskImage};});
  expect(header.content).not.toBe('none');
  expect(header.height).toBe('14px');
  expect(header.filter).toContain('blur(8px)');
  expect(header.pointer).toBe('none');
  expect(header.mask).toContain('linear-gradient');
  const zone=await page.locator('.composer-zone').evaluate(el=>{const s=getComputedStyle(el,'::before');return {content:s.content,height:s.height,filter:s.backdropFilter,pointer:s.pointerEvents};});
  expect(zone.content).not.toBe('none');
  expect(zone.height).toBe('18px');
  expect(zone.filter).toContain('blur(12px)');
  expect(zone.pointer).toBe('none');
  // Visual evidence: the expanded sidebar shows the compact bottom entries while the
  // header/composer fades sit at the transcript clip edges.
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await expect.poll(()=>page.locator('.sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().width))).toBeGreaterThan(250);
  const file='chat-opaque.png';
  await page.screenshot({path:process.env.PI_SCRATCH_DIR ? join(process.env.PI_SCRATCH_DIR,file) : test.info().outputPath(file)});
});

test('empty-state welcome stays centered on the composer band while the sidebar and window change',async({page})=>{
  for(const [w,h] of [[1440,900],[1080,850],[900,700],[860,700],[700,700],[620,700]]){
    for(const expanded of [false,true]){
      await page.setViewportSize({width:w,height:h});
      await page.goto('/?demo=1');
      await page.getByRole('button',{name:'新建会话',exact:true}).click();
      if(expanded){await page.getByRole('button',{name:'展开会话侧栏'}).click();await expect.poll(()=>page.locator('.sidebar').evaluate(el=>Math.round(el.getBoundingClientRect().width))).toBeGreaterThan(200);}
      const centers=await page.evaluate(()=>{
        const c=(sel: string)=>{const e=document.querySelector(sel);if(!e)return null;const r=e.getBoundingClientRect();return r.x+r.width/2;};
        return {welcome:c('.welcome'),heading:c('.welcome h2'),cards:c('.welcome-cards'),stack:c('.composer-stack'),main:c('.main-surface')};
      });
      expect(centers.welcome).not.toBeNull();
      expect(centers.cards).not.toBeNull();
      expect(Math.abs(centers.stack!-centers.main!)).toBeLessThanOrEqual(1);
      expect(Math.abs(centers.heading!-centers.stack!)).toBeLessThanOrEqual(1);
      expect(Math.abs(centers.cards!-centers.stack!)).toBeLessThanOrEqual(1);
      expect(Math.abs(centers.welcome!-centers.stack!)).toBeLessThanOrEqual(1);
    }
  }
});


test('sidebar dialogs center within the chat pane after resize and keep Escape dismissal',async({page})=>{
  await chat(page);
  await page.getByRole('button',{name:'展开会话侧栏'}).click();
  await page.getByRole('button',{name:'新建分区',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  for(const width of [1440,1080,800]){
    await page.setViewportSize({width,height:850});
    await expect.poll(()=>page.evaluate(()=>{
      const main=document.querySelector('.main-surface')!.getBoundingClientRect();
      const modal=document.querySelector('dialog[open]')!.getBoundingClientRect();
      return Math.abs((modal.left+modal.width/2)-(main.left+main.width/2));
    })).toBeLessThanOrEqual(1);
    const box=(await dialog.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x+box.width).toBeLessThanOrEqual(width);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});
