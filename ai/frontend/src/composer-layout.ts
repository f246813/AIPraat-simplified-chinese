import { useLayoutEffect, useState, type RefObject } from 'react';
import type { Editor } from '@tiptap/react';

/**
 * Content-height measurement inspired by PI-Desktop Composer.tsx (LGPL-3.0,
 * 0d47d26769ecbeca1c3ab56fa83b58a91de8190e). The fixed-width inert probe and
 * expand/shrink policy are project-local: never measure the widened editor,
 * otherwise wrapped text can repeatedly expand and collapse the composer.
 */
export function useComposerLayout(editor: Editor | null, form: RefObject<HTMLFormElement | null>, active: boolean) {
  const [wide, setWide] = useState(false);
  useLayoutEffect(() => {
    const zone = form.current?.closest<HTMLElement>('.composer-zone');
    const thread = zone?.closest<HTMLElement>('.thread');
    if (!active || !editor || !zone || !thread) return;
    const probe = document.createElement('div');
    probe.className = 'tiptap composer-editor-probe';
    probe.setAttribute('aria-hidden', 'true');
    probe.inert = true;
    zone.append(probe);
    let frame = 0;
    const measure = () => {
      if (editor.isDestroyed) return;
      const style = getComputedStyle(zone);
      const available = zone.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const compact = Math.min(available, parseFloat(getComputedStyle(thread).getPropertyValue('--conversation-width')) || 720);
      if (compact <= 0) return;
      probe.style.width = `${compact}px`;
      probe.replaceChildren(...Array.from(editor.view.dom.childNodes, node => node.cloneNode(true)));
      const metrics = getComputedStyle(probe);
      const height = probe.scrollHeight - parseFloat(metrics.paddingTop) - parseFloat(metrics.paddingBottom);
      // Paragraph spacing needs a small allowance; four visual lines widen.
      setWide(height / parseFloat(metrics.lineHeight) > 3.6);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule);
    observer.observe(thread);
    observer.observe(editor.view.dom);
    editor.on('transaction', schedule);
    measure();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); editor.off('transaction', schedule); probe.remove(); };
  }, [editor, form, active]);
  return wide;
}
