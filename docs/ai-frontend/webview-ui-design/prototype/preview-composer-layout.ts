// Preview-only adaptation of PI-Desktop's measured-content composer sizing.
// Upstream: vastsa/PI-Desktop, Composer.tsx at 0d47d26769ecbeca1c3ab56fa83b58a91de8190e (LGPL-3.0).
// Width switching and the stable-width Tiptap probe below are local additions,
// not copied from the unavailable Codex desktop renderer.
function usePreviewComposerLayout(editor: ReturnType<typeof useEditor>, form: {current: HTMLFormElement | null}) {
  const [wide, setWide] = useState(false);
  useLayoutEffect(() => {
    const zone = form.current?.closest<HTMLElement>('.composer-zone');
    if (!editor || !zone) return;
    const probe = document.createElement('div');
    probe.className = 'tiptap preview-editor-probe';
    probe.setAttribute('aria-hidden', 'true');
    probe.inert = true;
    zone.append(probe);
    let frame = 0;
    const measure = () => {
      const style = getComputedStyle(zone);
      const available = zone.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const compact = Math.min(available, parseFloat(getComputedStyle(zone.closest('.thread')!).getPropertyValue('--conversation-width')) || 720);
      // Measure at the compact width even while expanded; wrapping cannot cause
      // repeated expand/collapse when crossing the threshold.
      probe.style.width = `${compact}px`;
      probe.replaceChildren(...Array.from(editor.view.dom.childNodes, node => node.cloneNode(true)));
      const metrics = getComputedStyle(probe);
      const content = probe.scrollHeight - parseFloat(metrics.paddingTop) - parseFloat(metrics.paddingBottom);
      const rows = content / parseFloat(metrics.lineHeight);
      setWide(rows > 3.6);
      zone.dataset.previewRows = String(Math.ceil(rows));
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    const observer = new ResizeObserver(schedule);
    observer.observe(zone.closest('.thread')!);
    editor.on('transaction', schedule);
    measure();
    return () => { cancelAnimationFrame(frame); observer.disconnect(); editor.off('transaction', schedule); probe.remove(); };
  }, [editor, form]);
  return wide;
}
