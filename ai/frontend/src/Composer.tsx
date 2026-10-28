import { useEffect, useRef, useState } from 'react';
import { ComposerPrimitive, AttachmentPrimitive, useAuiState, type ThreadComposerRuntime } from '@assistant-ui/react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { useComposerLayout } from './composer-layout';
import { Bold, Italic, Code, List, Paperclip, ArrowUp, X, FileText } from 'lucide-react';
import { InputHistory, markdownParser, markdownSerializer, shouldSend } from './editor';
import { availableCommands, completionRange, detectCompletion, matchCommands, type CompletionDetection } from './completions';
import { ChatStore, shallowEqual, useChatSelector } from './store';
import type { Attachment, Preview, Session } from './types';
import { readOnly } from './types';
import { IconButton, Modal } from './ui';
import { safePreviewUrl } from './bridge';
import { ComposerStatus } from './ComposerStatus';
export const attachmentDescriptor = (a: Attachment) => ({id: a.id, type: a.mime.startsWith('image/') ? 'image' : 'file', name: a.name, contentType: a.mime, content: []});
export function AttachmentPreview({preview, onClose}: {preview: Preview; onClose: () => void}) {
  const url = safePreviewUrl(preview.dataUrl);
  return <Modal title={preview.name} onClose={onClose}><p className="muted">{preview.mime}</p>{preview.text !== undefined && <pre className="preview-text">{preview.text}</pre>}{url && (preview.mime.startsWith('image/') ? <img className="preview-image" alt={preview.name} src={url}/> : preview.mime.startsWith('audio/') ? <audio controls src={url}/> : null)}{!url && preview.text === undefined && <p>此格式暂不支持预览，可作为附件提交</p>}</Modal>;
}
export function Composer({store, session, composer, onSettings}: {store: ChatStore; session: Session; composer: ThreadComposerRuntime; onSettings: () => void}) {
  // Consume only the submit flag for this session and the settings this composer
  // reads; streaming text in any session never re-renders the editor host.
  const state = useChatSelector(store, s => ({boot: s.boot, submitting: s.submitting[session.id]}), shallowEqual); const [busy, setBusy] = useState(false); const [preview, setPreview] = useState<Preview>();
  const form = useRef<HTMLFormElement>(null); const composing = useRef(false); const compositionEnd = useRef(-Infinity); const history = useRef(new InputHistory());
  // The completion list is dismissed for one token only: retyping the same trigger reopens it.
  const [completion, setCompletion] = useState<(CompletionDetection & {caret: number}) | null>(null);
  const [highlight, setHighlight] = useState(0); const [dismissed, setDismissed] = useState<number | null>(null);
  const running = Boolean(store.running(session.id));
  const submitting = Boolean(state.submitting);
  const disabled=readOnly(session)||Boolean(session.archived);
  const commands = availableCommands(running, !disabled);
  const matches = completion ? matchCommands(commands, completion.query) : [];
  const completionOpen = Boolean(completion) && matches.length > 0 && dismissed !== completion!.start;
  const completionRef = useRef({open: false, count: 0, highlight: 0, matches});
  completionRef.current = {open: completionOpen, count: matches.length, highlight, matches};
  const closeCompletion = useRef<() => void>(() => {});
  closeCompletion.current = () => { setDismissed(completion?.start ?? null); setCompletion(null); };
  const acceptCompletion = useRef<(index: number) => void>(() => {});
  acceptCompletion.current = (index: number) => {
    const command = matches[index]; const editor = editorRef.current;
    if (!command || !completion || !editor) return;
    const {from, to} = completionRange(completion, completion.caret);
    editor.chain().focus().deleteRange({from, to}).run();
    setCompletion(null); setDismissed(null); setHighlight(0);
    if (command.id === 'new') void store.create().catch(store.report);
    else if (command.id === 'settings') onSettings();
    else if (command.id === 'stop') void store.cancel(session.id).catch(store.report);
    else void chooseFiles();
  };
  const editorRef = useRef<ReturnType<typeof useEditor>>(null);
  const latest = useRef({mode: String(state.boot?.settings.preferences.send_key || 'enter'), disabled});
  latest.current = {mode: String(state.boot?.settings.preferences.send_key || 'enter'), disabled};
  const editor = useEditor({
    extensions: [StarterKit.configure({underline: false, link: {openOnClick: false, autolink: false}})],
    content: '', immediatelyRender: true,
    editorProps: {
      attributes: {role: 'textbox', 'aria-label': '消息输入', 'aria-multiline': 'true', 'data-placeholder': '描述你的分析目标，或附上音频与材料…', spellcheck: 'false'},
      handleKeyDown: (view, event) => {
        const ime = composing.current || view.composing || event.isComposing || event.keyCode === 229;
        if (ime || performance.now() - compositionEnd.current < 90) return false;
        // An open list owns the navigation keys, so Enter accepts a command instead of sending.
        const list = completionRef.current;
        if (list.open) {
          if (event.key === 'ArrowDown') { event.preventDefault(); setHighlight(current => (current + 1) % list.count); return true; }
          if (event.key === 'ArrowUp') { event.preventDefault(); setHighlight(current => (current - 1 + list.count) % list.count); return true; }
          if (event.key === 'Escape') { event.preventDefault(); closeCompletion.current(); return true; }
          if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); acceptCompletion.current(list.highlight); return true; }
        }
        if (shouldSend(event, latest.current.mode, ime, false)) { event.preventDefault(); form.current?.requestSubmit(); return true; }
        if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && !event.ctrlKey && !event.metaKey && !event.shiftKey && (event.altKey || !view.state.doc.textContent.trim())) {
          const entries = (store.getSnapshot().messages[session.id] || []).filter(m => m.role === 'user' && m.content.trim()).map(m => m.content).reverse();
          const current = composer.getState().text;
          const recalled = history.current.navigate(event.key === 'ArrowUp' ? 'older' : 'newer', current, entries);
          if (recalled !== undefined) { event.preventDefault(); composer.setText(recalled); return true; }
        }
        return false;
      },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (!files.length) return false;
        event.preventDefault(); void addFiles(files); return true;
      },
      handleDrop: (_view, event) => {
        const files = Array.from(event.dataTransfer?.files || []);
        if (!files.length) return false;
        event.preventDefault(); void addFiles(files); return true;
      },
    },
    onUpdate: ({editor}) => { composer.setText(markdownSerializer.serialize(editor.state.doc)); syncCompletion(editor); },
    onSelectionUpdate: ({editor}) => syncCompletion(editor),
  });
  const wide = useComposerLayout(editor, form, !session.archived && !readOnly(session));
  editorRef.current = editor;
  /** Detection runs on the plain text before the caret, not on the serialized Markdown. */
  function syncCompletion(instance: typeof editor) {
    if (!instance) return;
    const caret = instance.state.selection.from;
    const before = instance.state.doc.textBetween(0, caret, '\n');
    const detection = detectCompletion(before);
    setCompletion(detection ? {...detection, caret} : null);
    if (detection) setDismissed(current => current !== null && current !== detection.start ? null : current);
  }
  async function addFiles(files: File[]) {
    if (latest.current.disabled) return;
    setBusy(true);
    try { for (const file of files) await composer.addAttachment(file); }
    catch (e) { store.report(e); } finally { setBusy(false); }
  }
  useEffect(() => {
    if (!editor) return;
    const sync = () => {
      const text = composer.getState().text;
      if (markdownSerializer.serialize(editor.state.doc) !== text && !editor.view.composing) {
        editor.commands.setContent(markdownParser(editor.schema).parse(text).toJSON(), {emitUpdate: false});
        editor.commands.setTextSelection(editor.state.doc.content.size - 1);
      }
      const attachments = composer.getState().attachments;
      // Persist only imported metadata per UI session; not File bytes or Base64.
      store.attachments.set(session.id, attachments.filter(a => a.status.type !== 'running').map(a => ({id: a.id, name: a.name, mime: a.contentType || 'application/octet-stream', size: 0})));
    };
    sync(); return composer.subscribe(sync);
  }, [composer, editor, store, session.id]);
  useEffect(() => { editor?.setEditable(!disabled && !state.submitting); }, [editor, disabled, session.id, state.submitting]);
  const chooseFiles = async () => {
    setBusy(true);
    try { const attachments = await store.rpc('attachments.choose', {}); for (const a of attachments) await composer.addAttachment(attachmentDescriptor(a)); }
    catch (e) { store.report(e); } finally { setBusy(false); }
  };
  const NativeAttachment = useRef(function NativeAttachment() {
    const attachment = useAuiState(s => s.attachment);
    return <AttachmentPrimitive.Root className="attachment-chip"><button type="button" onClick={() => void store.rpc('attachments.preview',{attachmentId: attachment.id}).then(setPreview).catch(store.report)}><FileText size={14}/><span>{attachment.name}</span>{attachment.status.type === 'running' && <small>导入中</small>}{attachment.status.type === 'incomplete' && <small>导入失败</small>}</button><AttachmentPrimitive.Remove aria-label={`移除 ${attachment.name}`} title={`移除 ${attachment.name}`}><X size={13}/></AttachmentPrimitive.Remove></AttachmentPrimitive.Root>;
  }).current;
  if (session.archived) return <footer className="readonly-footer">此会话已归档 · 恢复后可继续聊天</footer>;
  if (readOnly(session)) return <footer className="readonly-footer">旧记录只读 · 不恢复或重放旧操作 <button onClick={() => void store.create().catch(store.report)}>新建会话继续</button></footer>;
  return <div className={`composer-zone${wide ? ' composer-wide' : ''}`}><div className="composer-stack">{completionOpen && <div className="completion-list" role="listbox" aria-label="命令补全">{matches.map((command, index) => <button key={command.id} type="button" role="option" aria-selected={index === highlight} className={`completion-item ${index === highlight ? 'selected' : ''}`} onMouseDown={event => { event.preventDefault(); acceptCompletion.current(index); }} onMouseEnter={() => setHighlight(index)}><code>/{command.names[0]}</code><span>{command.label}</span><small>{command.hint}</small></button>)}</div>}<ComposerPrimitive.Root ref={form} className="composer" onSubmitCapture={e => { if (composing.current || performance.now() - compositionEnd.current < 90 || busy) e.preventDefault(); }} onDrop={e => { if (e.dataTransfer.files.length) { e.preventDefault(); e.stopPropagation(); void addFiles(Array.from(e.dataTransfer.files)); } }} onDragOver={e => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); }}>
    <div className="editor-toolbar" role="toolbar" aria-label="文本格式"><IconButton label="加粗（Ctrl+B）" aria-pressed={editor?.isActive('bold')} onClick={() => editor?.chain().focus().toggleBold().run()}><Bold size={15}/></IconButton><IconButton label="斜体（Ctrl+I）" onClick={() => editor?.chain().focus().toggleItalic().run()}><Italic size={15}/></IconButton><IconButton label="行内代码" onClick={() => editor?.chain().focus().toggleCode().run()}><Code size={15}/></IconButton><IconButton label="项目列表" onClick={() => editor?.chain().focus().toggleBulletList().run()}><List size={15}/></IconButton></div>
    <div onInputCapture={e => { if (!(e.nativeEvent as InputEvent).isComposing) composing.current = false; }} onCompositionStartCapture={() => { composing.current = true; }} onCompositionEndCapture={() => { composing.current = false; compositionEnd.current = performance.now(); }}><EditorContent editor={editor}/></div><div className="attachment-list"><ComposerPrimitive.Attachments components={{Attachment: NativeAttachment}}/></div>
    <div className="composer-actions"><button type="button" className="attach-button" disabled={busy} onClick={() => void chooseFiles()}><Paperclip size={16}/>{busy ? '导入中…' : '添加附件'}</button><ComposerStatus store={store} session={session} composer={composer} onSettings={onSettings}/>{running ? <ComposerPrimitive.Cancel className="send-button" aria-label="暂停当前任务" title="暂停当前任务"><span className="send-loading-square" aria-hidden="true"/></ComposerPrimitive.Cancel> : <ComposerPrimitive.Send className="send-button" aria-label={submitting ? '正在发送' : '发送消息'} disabled={busy || submitting} onClickCapture={e => { if (composing.current || editor?.view.composing || performance.now() - compositionEnd.current < 90 || busy) { e.preventDefault(); e.stopPropagation(); } }}>{submitting ? <span className="send-loading-square" aria-hidden="true"/> : <ArrowUp size={20}/>}</ComposerPrimitive.Send>}</div>
  </ComposerPrimitive.Root></div>{preview && <AttachmentPreview preview={preview} onClose={() => setPreview(undefined)}/>}</div>;
}
