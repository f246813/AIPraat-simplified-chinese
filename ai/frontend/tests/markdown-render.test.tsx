import {act, cleanup, fireEvent, render} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {Markdown} from '../src/Markdown';
import type {Rpc} from '../src/types';

// Markdown mounts through useSmoothText, which reads the reduced-motion query.
beforeEach(() => {
  const media = Object.assign(new EventTarget(), {matches: true}); // no animation: authoritative text shows at once
  vi.stubGlobal('matchMedia', () => media);
});
afterEach(() => {cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();});

const noop = () => {};

describe('Markdown block rendering', () => {
  it('renders multi-block content — headings, tables, code, math, CJK and emoji — to the right structure', () => {
    const rpc = vi.fn() as unknown as Rpc;
    const source = '# 标题\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n```js\nconst x = 1;\n```\n\n中文 ✅ emoji 😀 与 $e^{i\\pi}+1=0$ 同行。\n';
    const {container} = render(<Markdown text={source} rpc={rpc} onError={noop}/>);
    expect(container.querySelector('h1')?.textContent).toBe('标题');
    expect(container.querySelectorAll('tbody tr')).toHaveLength(1);
    const code = container.querySelector('pre code');
    expect(code?.textContent).toContain('const x = 1');
    expect(code?.className).toContain('language-js');
    // KaTeX renders the inline math node.
    expect(container.querySelector('.katex')).toBeTruthy();
    expect(container.textContent).toContain('中文');
    expect(container.textContent).toContain('😀');
  });

  it('keeps a settled code block mounted while a normal tail grows, without duplicating it', () => {
    const rpc = vi.fn() as unknown as Rpc;
    const settled = 'Intro.\n\n```python\nprint("kept")\n```\n';
    const {container, rerender} = render(<Markdown text={settled} rpc={rpc} onError={noop}/>);
    const firstCode = container.querySelector('pre');
    expect(firstCode?.textContent).toContain('print("kept")');
    // A normal append: the settled block keeps the same content and index.
    rerender(<Markdown text={`${settled}\nMore prose arrives here.\n`} rpc={rpc} onError={noop}/>);
    expect(container.querySelector('pre')).toBe(firstCode); // same DOM node — not remounted
    expect(container.querySelectorAll('pre')).toHaveLength(1);
    expect(container.textContent).toContain('More prose arrives here.');
  });

  it('routes links through the host rpc and blocks external images', () => {
    const rpc = vi.fn(async () => ({}));
    const {container} = render(<Markdown text={'See [the docs](https://example.com) and ![pic](http://x/y.png).\n'} rpc={rpc as unknown as Rpc} onError={noop}/>);
    const anchor = container.querySelector('a')!;
    expect(anchor.getAttribute('href')).toBe('https://example.com');
    act(() => { anchor.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); });
    expect(rpc).toHaveBeenCalledWith('links.open', {url: 'https://example.com'});
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('.blocked-image')?.textContent).toContain('外部图片已阻止');
  });

  it('applies an authoritative replacement without a stale prefix', () => {
    const rpc = vi.fn() as unknown as Rpc;
    const {container, rerender} = render(<Markdown text={'第一段很长很长的原始正文内容。\n\n```js\nold\n```\n'} rpc={rpc} onError={noop}/>);
    rerender(<Markdown text={'修正后的短正文。'} rpc={rpc} onError={noop}/>);
    expect(container.querySelector('pre')).toBeNull();
    expect(container.textContent).toBe('修正后的短正文。');
  });

  it.each([false, true])('bounds a long receiving tail with smooth=%s and restores rich text on completion', smooth => {
    const rpc = vi.fn() as unknown as Rpc;
    const text = 'Intro.\n\n```text\n' + '中文😀'.repeat(10000);
    const {container, rerender} = render(<Markdown text={'Intro.\n\n```text\n'} rpc={rpc} onError={noop} streaming smooth={smooth}/>);
    rerender(<Markdown text={text} rpc={rpc} onError={noop} streaming smooth={smooth}/>);
    expect(container.querySelector('.markdown-plain-fallback pre')?.textContent === text.slice('Intro.\n\n'.length)).toBe(true);
    expect(container.querySelector('pre code')).toBeNull();
    rerender(<Markdown text={text + '\n```'} rpc={rpc} onError={noop} smooth={smooth}/>);
    expect(container.querySelector('.markdown-plain-fallback')).toBeNull();
    expect(container.querySelector('pre code')?.textContent).toBe('中文😀'.repeat(10000) + '\n');
  });

  it('copies each code node without fences or neighboring prose in a document with a footnote', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {value: {writeText}, configurable: true});
    const text = 'A[^1].\n\n```python\nprint("ok")\n```\n\nBetween.\n\n> ```js\n> console.log(2);\n> ```\n\n    indented code\n\n[^1]: note\n';
    const {container} = render(<Markdown text={text} rpc={vi.fn() as unknown as Rpc} onError={noop}/>);
    const buttons = container.querySelectorAll('[aria-label="复制代码"]');
    expect(buttons).toHaveLength(3);
    for (const button of buttons) await act(async () => {fireEvent.click(button);});
    expect(writeText.mock.calls.map(([text]) => text)).toEqual(['print("ok")\n', 'console.log(2);\n', 'indented code\n']);
  });

  it('copies the received code before its animation completes, without later blocks', async () => {
    const media = Object.assign(new EventTarget(), {matches: false});
    vi.stubGlobal('matchMedia', () => media);
    let now = 100; const frames = new Map<number, FrameRequestCallback>(); let serial = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {frames.set(++serial, callback); return serial;});
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {value: {writeText}, configurable: true});
    const code = 'print("complete received code")\n';
    const {container} = render(<Markdown text={'```python\n' + code + '```\n\nLater prose.\n\n```js\nother()\n```'} rpc={vi.fn() as unknown as Rpc} onError={noop} smooth fromStart/>);
    for (let i = 0; i < 9; i++) act(() => {now += 20; const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now));});
    expect(container.querySelector('pre code')?.textContent?.length).toBeLessThan(code.length);
    await act(async () => {fireEvent.click(container.querySelector('[aria-label="复制代码"]')!);});
    expect(writeText).toHaveBeenCalledWith(code);
  });

  it.each([
    'A[^1].\n\n```mermaid\ngraph LR\nA-->B\n```\n\n[^1]: note\n',
    '> ```mermaid\n> graph LR\n> A-->B\n> ```\n',
    '- ```mermaid\n  graph LR\n  A-->B\n  ```\n',
    '\n\n```mermaid\ngraph LR\nA-->B\n```\n',
  ])('routes a closed Mermaid node to the diagram renderer: %s', text => {
    const {container} = render(<Markdown text={text} rpc={vi.fn() as unknown as Rpc} onError={noop}/>);
    expect(container.querySelector('.diagram')).toBeTruthy();
    expect(container.querySelector('.diagram pre')?.textContent).toBe('graph LR\nA-->B');
  });

  it.each(['```mermaid\ngraph LR\nA-->B', '> ```mermaid\n> graph LR\n> A-->B', 'A[^1].\n\n[^1]: note\n\n```mermaid\ngraph LR\nA-->B'])('keeps an unclosed Mermaid node as code: %s', text => {
    const {container} = render(<Markdown text={text} rpc={vi.fn() as unknown as Rpc} onError={noop} streaming/>);
    expect(container.querySelector('.diagram')).toBeNull();
    expect(container.querySelector('pre code')?.textContent).toBe('graph LR\nA-->B\n');
  });
});
