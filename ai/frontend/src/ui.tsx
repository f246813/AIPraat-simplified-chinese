import { useLayoutEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
export const hintText = (text: string) => text.replace(/[。.](?=\s*(?:\n|$))/g, '').replace(/。/g, '；');
export function IconButton({label, children, ...props}: ButtonHTMLAttributes<HTMLButtonElement> & {label: string}) { return <button type="button" className="icon-button" title={label} aria-label={label} {...props}>{children}</button>; }
export function Modal({title, children, onClose}: {title: string; children: ReactNode; onClose: () => void}) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current!;
    const pane = dialog.closest('.app')?.querySelector<HTMLElement>(':scope > .main-surface');
    const align = () => {
      const rect = pane?.getBoundingClientRect();
      dialog.style.setProperty('--modal-left', `${rect?.left ?? 0}px`);
      dialog.style.setProperty('--modal-right', `${rect ? innerWidth - rect.right : 0}px`);
    };
    align();
    dialog.showModal();
    const observer = pane ? new ResizeObserver(align) : null;
    if (pane) observer?.observe(pane);
    window.addEventListener('resize', align);
    return () => { observer?.disconnect(); window.removeEventListener('resize', align); };
  }, []);
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><header><h2>{title}</h2><IconButton label="关闭" onClick={onClose}>×</IconButton></header>{children}</dialog>;
}
export function Json({value}: {value: unknown}) { return <pre className="json">{JSON.stringify(value, null, 2)}</pre>; }
export const statusLabel = (status?: string) => ({running: '运行中', queued: '排队中', pending: '等待中', submitted: '已提交', complete: '已完成', completed: '已完成', success: '成功', failed: '失败', error: '错误', cancelled: '已取消', cancelling: '取消中', interrupted: '已中断', partial: '部分结果', unknown: '状态未知'}[status || ''] || status || '状态未提供');
