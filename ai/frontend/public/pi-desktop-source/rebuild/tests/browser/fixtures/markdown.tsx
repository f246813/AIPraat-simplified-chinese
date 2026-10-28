import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Markdown} from '../../../src/Markdown';
import type {Rpc} from '../../../src/types';
import '../../../src/styles.css';

const rpc: Rpc = async () => {throw new Error('The rendering fixture has no host RPC.');};
const onError = (error: unknown) => {throw error;};
const long = '```text\n' + '中文😀'.repeat(10000);
const diagrams = 'A[^1].\n\n```python\nprint("ok")\n```\n\n```mermaid\ngraph LR\nA-->B\n```\n\n> ```mermaid\n> graph LR\n> C-->D\n> ```\n\n[^1]: note\n';

function Fixture() {
  const receiving = new URLSearchParams(location.search).get('case') === 'long';
  const [streaming, setStreaming] = useState(receiving);
  return <main style={{maxWidth: 800, margin: '24px auto'}}>
    {receiving && <button onClick={() => setStreaming(false)}>完成接收</button>}
    <Markdown text={receiving ? long + (streaming ? '' : '\n```') : diagrams} rpc={rpc} onError={onError} streaming={streaming}/>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
