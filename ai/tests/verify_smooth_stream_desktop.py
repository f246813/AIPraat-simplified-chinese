"""Verify reply pacing in the built React UI and real WebView2 with offline text."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window

BODY = '这是用于验证平滑显示的离线文字，整段回复与任务完成同时交付，不调用模型或 Praat。' * 8


class Executor:
    def capture_target(self, text):
        return 'offline-render-fixture'

    def run(self, **values):
        values['emit']('delta', {'text': BODY})
        return {'content': BODY, 'status': 'complete'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import webview
    for key in list(os.environ):
        if key.startswith('PRAAT_AI_'):
            os.environ.pop(key)
    result = {'realCloud': False, 'realPraat': False, 'execution': 'offline text fixture'}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config = root / 'config.json'
        config.write_text(json.dumps({'api': {'enabled': True, 'locked': False,
            'base_url': 'http://127.0.0.1:8999/v1', 'model': 'offline-render-fixture',
            'token_mode': 'manual', 'max_context_tokens': 32768, 'stop_local_service': False},
            'modern': {'preferences': {'smooth_stream': True}}}), encoding='utf8')
        app = ModernApplication(root / 'modern', config, root / 'legacy.sqlite3', executor=Executor())
        window, assets = create_window(app)

        def check():
            js = window.evaluate_js

            def wait(code, timeout=15):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    if js(code):
                        return
                    time.sleep(.05)
                raise AssertionError('WebView2 condition timed out: ' + code)

            try:
                wait("!!document.querySelector('[aria-label=发送消息]')")
                result['userAgent'] = js('navigator.userAgent')
                result['reducedMotion'] = js("matchMedia('(prefers-reduced-motion: reduce)').matches")
                js("""window.__smoothSamples=[];
                    window.__smoothObserver=new MutationObserver(()=>{
                      const text=document.querySelector('.message.assistant > .message-content > .prose')?.textContent || '';
                      if(text && window.__smoothSamples.at(-1)?.text!==text)
                        window.__smoothSamples.push({at:performance.now(),text});
                    });
                    window.__smoothObserver.observe(document.querySelector('.transcript'),{subtree:true,childList:true,characterData:true});
                    const input=document.querySelector('[role=textbox][aria-label=消息输入]');
                    input.focus();document.execCommand('insertText',false,'验证平滑显示');""")
                time.sleep(.15)
                js("document.querySelector('[aria-label=发送消息]').click()")
                wait("document.querySelector('.message.assistant > .message-content > .prose')?.textContent === " + json.dumps(BODY))
                samples = js('window.__smoothSamples')
                result['samples'] = [{'at': item['at'], 'length': len(item['text'])} for item in samples]
                result['finalTextMatches'] = samples[-1]['text'] == BODY
                assert result['finalTextMatches']
                if not result['reducedMotion']:
                    assert len(samples) > 10, samples
                    assert len(samples[0]['text']) < len(BODY) / 2, samples[0]
                result['passed'] = True
            except Exception as error:
                result['passed'] = False
                result['error'] = repr(error)
            finally:
                app.close()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
                window.destroy()

        try:
            webview.start(check, gui='edgechromium', private_mode=True, storage_path=str(root / 'profile'))
        finally:
            app.close()
            assets.close()
    print(json.dumps({key: result.get(key) for key in ('passed', 'reducedMotion', 'finalTextMatches', 'error')}, ensure_ascii=False))
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
