import { createRoot } from 'react-dom/client';
import { App } from './App';
import { HostBridge } from './bridge';
import { ChatStore } from './store';
import './styles.css';
import './pi/styles.css';
import { installScrollbarReveal } from './pi/scrollbar-reveal';
async function main() {
  const cleanupScrollbars = installScrollbarReveal(document);
  if (import.meta.hot) import.meta.hot.dispose(cleanupScrollbars);
  if (new URLSearchParams(location.search).get('window') === 'pronunciation') {
    const { PronunciationForm } = await import('./PronunciationForm');
    createRoot(document.getElementById('root')!).render(<PronunciationForm/>);
    return;
  }
  if (new URLSearchParams(location.search).get('window') === 'anki') {
    const { AnkiIntegration } = await import('./AnkiIntegration');
    createRoot(document.getElementById('root')!).render(<AnkiIntegration/>);
    return;
  }
  if (new URLSearchParams(location.search).get('window') === 'dictionaries') {
    const { SpeechDictionaries } = await import('./SpeechDictionaries');
    createRoot(document.getElementById('root')!).render(<SpeechDictionaries/>);
    return;
  }
  const explicitDemo = import.meta.env.DEV && new URLSearchParams(location.search).get('demo') === '1' && ['127.0.0.1','localhost'].includes(location.hostname) && !window.pywebview;
  const adapter = explicitDemo ? (await import('./demo')).createDemoAdapter() : undefined;
  const bridge = new HostBridge(adapter); const store = new ChatStore(bridge.rpc);
  createRoot(document.getElementById('root')!).render(<App bridge={bridge} store={store} demo={explicitDemo}/>);
}
void main();
