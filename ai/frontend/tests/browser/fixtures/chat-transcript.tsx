import {createRoot} from 'react-dom/client';
import {App} from '../../../src/App';
import {HostBridge} from '../../../src/bridge';
import {ChatStore} from '../../../src/store';
import {createDemoAdapter} from '../../../src/demo';
import type {Message, Methods, Rpc} from '../../../src/types';
import '../../../src/styles.css';
import '../../../src/pi/styles.css';

const demo = createDemoAdapter();
const added: Message[] = [];
const rpc = (async (method: keyof Methods, params: never) => {
  const result = await demo(method, params);
  if (method === 'sessions.get' && (params as {sessionId: string}).sessionId === 'demo-history') {
    const history = result as Methods['sessions.get'][1];
    return {...history, messages: [...history.messages, ...added]};
  }
  return result;
}) as Rpc;
const bridge = new HostBridge(rpc);
const store = new ChatStore(bridge.rpc);
Object.assign(window, {chatTranscriptFixture: {
  append: async () => {
    added.push({id: `appended-${added.length}`, role: 'assistant', content: 'New output while reading history', status: 'complete'});
    await store.load('demo-history');
  },
  saved: () => store.getView('demo-history'),
}});
createRoot(document.getElementById('root')!).render(<App bridge={bridge} store={store} demo/>);
