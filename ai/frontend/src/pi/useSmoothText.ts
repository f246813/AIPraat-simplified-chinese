// SPDX-License-Identifier: LGPL-3.0
// Adapted from PI-Desktop 8f4285fd2f25380f2f5ed0b6f645b6623418e3f3.
// Source and adaptation details: ../../../third_party/pi-desktop/NOTICE.md
import { useEffect, useRef, useState } from "react";
const MAX_SMOOTH_TEXT_CODE_UNITS = 32 * 1024;

/** Releases live reply text using PI-Desktop's adaptive frame pacing.
 * New replies start empty and drain after completion; loaded history starts full.
 */
export function useSmoothText(
  source: string,
  enabled: boolean,
  fromStart = false,
): string {
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const shouldSmooth = enabled && !reducedMotion && source.length <= MAX_SMOOTH_TEXT_CODE_UNITS;
  const [revealed, setRevealed] = useState(fromStart ? 0 : source.length);
  const revealedRef = useRef(fromStart ? 0 : source.length);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef(0);
  const fractionalAdvanceRef = useRef(0);

  const previousSource = useRef(source);
  const replaced = !source.startsWith(previousSource.current);
  useEffect(() => { previousSource.current = source; }, [source]);

  // Replacements and disabled animation show the authoritative text immediately.
  useEffect(() => {
    if (!shouldSmooth || replaced) {
      revealedRef.current = source.length;
      lastFrameRef.current = 0;
      fractionalAdvanceRef.current = 0;
      setRevealed(source.length);
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    }
  }, [shouldSmooth, source.length, replaced]);

  // Core release loop
  useEffect(() => {
    if (!shouldSmooth || replaced || revealedRef.current >= source.length) return;

    const tick = (now: number) => {
      const backlog = source.length - revealedRef.current;
      if (backlog <= 0) {
        // The source dependency restarts this effect when more text arrives.
        rafRef.current = null;
        lastFrameRef.current = 0;
        fractionalAdvanceRef.current = 0;
        return;
      }

      const elapsed = now - lastFrameRef.current;
      // Keep React and Markdown commits at or below 60 Hz on high-refresh screens.
      if (elapsed < 1000 / 60) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      lastFrameRef.current = now;

      // Base speed is ~60 chars/sec; larger backlogs increase the release rate.
      const baseCharsPerSec = 60;
      const maxLagChars = 30;
      const speed =
        backlog > maxLagChars
          ? backlog / 0.5
          : baseCharsPerSec;

      const dt = Math.min(elapsed, 100) / 1000; // cap dt to avoid big jumps
      // Carry fractional characters so skipped frames do not slow the reveal.
      const exactAdvance = fractionalAdvanceRef.current + speed * dt;
      const advance = Math.floor(exactAdvance);
      fractionalAdvanceRef.current = exactAdvance - advance;
      if (advance === 0) {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      const next = Math.min(revealedRef.current + advance, source.length);

      revealedRef.current = next;
      setRevealed(next);

      rafRef.current = requestAnimationFrame(tick);
    };

    if (lastFrameRef.current === 0) {
      lastFrameRef.current = performance.now();
    }
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [shouldSmooth, source, replaced]);

  // Release the scheduled frame on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, []);

  if (!shouldSmooth || replaced) return source;
  // Avoid slicing in the middle of a UTF-16 surrogate pair.
  let end = revealed;
  if (end < source.length && end > 0) {
    const code = source.charCodeAt(end - 1);
    // High surrogate without its low surrogate: include both.
    if (code >= 0xd800 && code <= 0xdbff) end = Math.min(end + 1, source.length);
  }
  return source.slice(0, end);
}
