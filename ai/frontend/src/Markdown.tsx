// SPDX-License-Identifier: LGPL-3.0
// Block-splitting and the stable component-map / block-memo pattern are adapted
// from PI-Desktop 9f313c8f855ef6f8311622ad63dd7e6583cef2d7 (Markdown.tsx +
// lib/markdown-blocks.ts). Local runtime (KaTeX, highlight.js,
// Mermaid, host-mediated links, sanitization and safety rules) is project-owned.
// Details: ../third_party/pi-desktop/NOTICE.md
import { createContext, memo, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown, { type Components, type Options } from 'react-markdown';
import { useSmoothText } from './pi/useSmoothText';
import { markdownRemarkPlugins, advanceMarkdownBlocks, emptyMarkdownBlockCache, receivedCodeText, type MarkdownBlockCache } from './pi/markdown-blocks';
import {codeTextContent} from './markdown-code';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import DOMPurify from 'dompurify';
import { Copy } from 'lucide-react';
import { IconButton } from './ui';
import type { Rpc } from './types';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

// Streaming tail budget in UTF-16 code units. This is an internal render
// parameter for the growing, unclosed tail, not a host reply-length limit; the
// authoritative text is never trimmed to fit it.
const MAX_STREAMING_TAIL_CODE_UNITS = 32 * 1024;

const schema = {...defaultSchema, attributes: {...defaultSchema.attributes, code: [['className', /^language-./, 'math-inline', 'math-display'], 'dataFenceClosed']}};
// The splitter and every per-block ReactMarkdown share the exact same remark
// grammar, so a block boundary and the rendered node can never disagree. The
// arrays are module constants: a fresh plugin array each render would rebuild
// every processor and defeat the block memo.
const remarkPlugins = markdownRemarkPlugins;
const rehypePlugins = [[rehypeSanitize, schema], [rehypeKatex, {trust: false, strict: 'ignore'}], [rehypeHighlight, {detect: false}]] as Options['rehypePlugins'];

type Service = {rpc: Rpc; onError: (e: unknown) => void; readCode: (start: number | undefined, fallback: string) => string};
const ServiceContext = createContext<Service>(null!);
const useService = () => useContext(ServiceContext);
const BlockContext = createContext(0);

let diagramQueue = Promise.resolve();
function Diagram({source}: {source: string}) {
  const id = useId().replace(/[^a-z0-9]/gi, '');
  const [svg, setSvg] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setSvg(''); setError('');
    if (source.length > 24000) { setError('图表超过 24,000 字符，请查看源码'); return; }
    diagramQueue = diagramQueue.then(async () => {
      // A superseded render task checks its own validity before doing work.
      if (!live) return;
      try {
        // Official prebuilt browser ESM and its chunks are copied offline at prebuild.
        const moduleUrl = new URL(`${import.meta.env.BASE_URL}vendor/mermaid/mermaid.esm.min.mjs`, document.baseURI).href;
        const {default: mermaid} = await import(/* @vite-ignore */ moduleUrl) as typeof import('mermaid');
        if (!live) return;
        mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', flowchart: {htmlLabels: false}, suppressErrorRendering: true});
        const result = await mermaid.render(`diagram${id}`, source);
        const clean = DOMPurify.sanitize(result.svg, {USE_PROFILES: {svg: true, svgFilters: true}, FORBID_TAGS: ['foreignObject', 'script', 'a', 'image'], FORBID_ATTR: ['href', 'xlink:href']});
        if (live) setSvg(clean);
      } catch { if (live) setError('图表格式有误；可展开查看源码'); }
    });
    return () => { live = false; };
  }, [source, id]);
  return <div className="diagram">{svg ? <div role="img" aria-label="Mermaid 图表" dangerouslySetInnerHTML={{__html: svg}} /> : <small>{error || '正在渲染图表…'}</small>}<details><summary>图表源码</summary><pre>{source}</pre></details></div>;
}

function CodeBlock({children, language, start, source}: {children: ReactNode; language: string; start: number | undefined; source: string}) {
  const {onError, readCode} = useService();
  return <div className="code-block"><div className="code-header"><small>{language}</small><IconButton label="复制代码" onClick={() => void navigator.clipboard.writeText(readCode(start, source)).catch(onError)}><Copy size={14}/></IconButton></div><pre>{children}</pre></div>;
}

// Stable component references: hoisted to module scope so a growing reply never
// remounts the subtree or rebuilds the ReactMarkdown component map each frame.
const LinkAnchor: NonNullable<Components['a']> = ({href, children}) => {
  const {rpc, onError} = useService();
  return <a href={/^https?:\/\//i.test(href || '') ? href : undefined} onClick={e => { e.preventDefault(); if (href) void rpc('links.open', {url: href}).catch(onError); }}>{children}</a>;
};
const BlockedImage: NonNullable<Components['img']> = ({alt}) => <span className="blocked-image">[外部图片已阻止：{alt || '图片'}]</span>;
const PreBlock: NonNullable<Components['pre']> = ({node, children}) => {
  const offset = useContext(BlockContext);
  const code = node?.children.find(child => child.type === 'element' && child.tagName === 'code');
  const source = codeTextContent(code);
  const classes = code?.type === 'element' ? String(code.properties.className ?? '') : '';
  const closedFence = code?.type === 'element' && code.properties.dataFenceClosed === true;
  const localStart = node?.position?.start.offset;
  if (classes.includes('language-mermaid') && closedFence) return <Diagram source={source.replace(/\n$/, '')} />;
  return <CodeBlock language={classes.match(/language-(\w+)/)?.[1] || '代码'} start={localStart === undefined ? undefined : offset + localStart} source={source}>{children}</CodeBlock>;
};
const markdownComponents: Components = {a: LinkAnchor, img: BlockedImage, pre: PreBlock};

function useBlocks(source: string, maxTailCodeUnits: number): string[] {
  const cache = useRef<MarkdownBlockCache>(emptyMarkdownBlockCache);
  return useMemo(() => {
    cache.current = advanceMarkdownBlocks(cache.current, source, maxTailCodeUnits);
    return cache.current.blocks;
  }, [source, maxTailCodeUnits]);
}

// One memoized block. Settled blocks keep their string content and offsets, so
// the default shallow compare skips them while only the growing tail re-renders.
const Block = memo(function MarkdownBlock({raw, offset}: {raw: string; offset: number}) {
  return <BlockContext.Provider value={offset}><ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} skipHtml components={markdownComponents}>{raw}</ReactMarkdown></BlockContext.Provider>;
});

export const Markdown = memo(function Markdown({text, rpc, onError, smooth = false, fromStart = false, streaming = false}: {text: string; rpc: Rpc; onError: (e: unknown) => void; smooth?: boolean; fromStart?: boolean; streaming?: boolean}) {
  const visible = useSmoothText(text, smooth, fromStart);
  const received = useRef(text);
  received.current = text;
  const readCode = useCallback((start: number | undefined, fallback: string) => start === undefined ? fallback : receivedCodeText(received.current, start) ?? fallback, []);
  const service = useMemo(() => ({rpc, onError, readCode}), [rpc, onError, readCode]);
  const growing = streaming || visible !== text;
  const blocks = useBlocks(visible, growing ? MAX_STREAMING_TAIL_CODE_UNITS : Number.POSITIVE_INFINITY);
  let offset = 0;
  return <div className="prose"><ServiceContext.Provider value={service}>{blocks.map((raw, i) => {
    const start = offset; offset += raw.length;
    // An unclosed tail over budget keeps complete text as plain text rather than
    // re-parsing it from its first byte every frame.
    if (growing && raw.length > MAX_STREAMING_TAIL_CODE_UNITS) {
      return <div className="markdown-plain-fallback" key={i} data-source-start={start} data-source-end={start + raw.length}><div role="status">内容较长，正在按纯文本显示…</div><pre>{raw}</pre></div>;
    }
    return <Block key={i} raw={raw} offset={start} />;
  })}</ServiceContext.Provider></div>;
});
