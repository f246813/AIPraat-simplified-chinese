import type { Methods, Rpc } from './types';
declare global { interface Window { pywebview?: {api?: {rpc?: (method: string, params: unknown) => Promise<unknown>}} } }
const allowed = new Set<keyof Methods>(['bootstrap', 'sessions.create', 'sessions.get', 'sessions.search', 'sessions.context', 'window.drag', 'sessions.rename', 'sessions.pin', 'sessions.delete', 'sessions.view', 'sessions.fork', 'sessions.section', 'sessions.archive', 'sessions.group', 'sections.create', 'sections.update', 'sections.delete', 'sections.archive', 'tasks.submit', 'tasks.cancel', 'events.poll', 'settings.get', 'settings.save', 'settings.test', 'settings.choose_model', 'attachments.choose', 'attachments.import', 'attachments.preview', 'links.open']);
export class HostBridge {
  constructor(private adapter?: Rpc) {}
  ready() { return Boolean(this.adapter || window.pywebview?.api?.rpc); }
  async wait(milliseconds = 8000) {
    if (this.ready()) return;
    await new Promise<void>((resolve, reject) => {
      const finish = () => { clearTimeout(timer); clearInterval(interval); window.removeEventListener('pywebviewready', check); };
      const check = () => { if (this.ready()) { finish(); resolve(); } };
      const timer = setTimeout(() => { finish(); reject(new Error('未连接桌面宿主，请从 AIPraat 打开 AI 工作台')); }, milliseconds);
      const interval = setInterval(check, 100);
      window.addEventListener('pywebviewready', check);
    });
  }
  rpc: Rpc = async (method, params) => {
    if (!allowed.has(method)) throw new Error('宿主接口不在白名单中');
    if (method === 'links.open' && !/^https?:\/\//i.test((params as Methods['links.open'][0]).url)) throw new Error('仅允许 HTTP / HTTPS 链接');
    if (this.adapter) return this.adapter(method, params);
    const api = window.pywebview?.api;
    if (!api?.rpc) throw new Error('桌面宿主未连接；操作未执行');
    return await api.rpc(method, params) as never;
  };
}
export async function importFile(rpc: Rpc, file: File) {
  if (file.size > 20 * 1024 * 1024) throw new Error('附件超过 20 MB；请缩小文件后重试（宿主还会校验自己的上限）');
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
    reader.readAsDataURL(file);
  });
  return rpc('attachments.import', {name: file.name, mime: file.type || 'application/octet-stream', data});
}
export function safePreviewUrl(value?: string) {
  // HTML/SVG are never displayed as active documents, even if returned by host.
  return value && /^data:(image\/(png|jpeg|gif|webp|bmp)|audio\/(wav|x-wav|mpeg|ogg|mp4));base64,[a-z\d+/=\r\n]+$/i.test(value) ? value : undefined;
}
