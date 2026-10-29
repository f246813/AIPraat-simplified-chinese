import {useLayoutEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {VirtualTranscript, type TranscriptHandle} from '../../../src/VirtualTranscript';
import type {Message, ReadingAnchor} from '../../../src/types';

const count = Number(new URLSearchParams(location.search).get('count') ?? 2000);
const messages: Message[] = Array.from({length: count}, (_, index) => ({id: `row-${index}`, role: 'assistant', content: `Message ${index}`}));
function Fixture() {
  const viewport = useRef<HTMLDivElement>(null);
  const list = useRef<TranscriptHandle>(null);
  const saved = useRef<{anchor: ReadingAnchor | null; scroll: number} | null>(null);
  const [width, setWidth] = useState(600);
  const [start, setStart] = useState(40);
  useLayoutEffect(() => {
    Object.assign(window, {transcriptFixture: {
      go: (index: number, offset: number) => list.current?.position(`row-${index}`, offset),
      latest: () => list.current?.scrollToLatest(),
      width: setWidth,
      saved: () => saved.current,
    }});
  }, []);
  return <div ref={viewport} className="fixture-viewport" style={{width, height: 480, overflow: 'auto', overflowAnchor: 'none'}}>
    <div style={{height: 71}}>History control</div>
    <VirtualTranscript ref={list} viewportRef={viewport} messages={messages} logicalStart={start}
      onAnchor={(anchor, scroll) => { saved.current = {anchor, scroll}; }} onFlush={() => {}}
      onReachTop={() => {}} onRevealTo={index => setStart(Math.min(index, start))} onFollowChange={() => {}}>
      {rows => rows.map(row => <div key={row.key} ref={row.measure} data-index={row.index} data-row-id={row.key}
        style={{position: 'absolute', top: row.top, width: '100%', boxSizing: 'border-box', paddingBottom: 20}}>
        <p style={{margin: 0, lineHeight: '20px'}}>{`${row.key} — ${'text wraps across the available width '.repeat(row.fullIndex % 7 + 1)}`}</p>
      </div>)}
    </VirtualTranscript>
  </div>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
