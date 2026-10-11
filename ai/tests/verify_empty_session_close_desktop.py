"""Verify an untouched production WebView2 window leaves no new history."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window


class NoExecution:
    def capture_target(self, text):
        raise AssertionError('Unexpected Praat capture')

    def run(self, **kwargs):
        raise AssertionError('Unexpected model execution')


def main():
    import webview
    for key in list(os.environ):
        if key.startswith('PRAAT_AI_'):
            os.environ.pop(key)
    result = {'passed': False, 'checks': [], 'realModel': False, 'realPraat': False}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = root / 'config.json'
        config.write_text(json.dumps({'api': {'enabled': False}, 'server': {'auto_start': False}}), encoding='utf8')
        app = ModernApplication(root / 'modern', config, executor=NoExecution())
        historic = app.store.new_session('已有对话')['id']
        app.store.put_message(historic, dict(id='historic-user', role='user', content='保留历史'))
        original = app.store.get(historic)
        window, assets = create_window(app, hidden=True)

        def check():
            try:
                deadline = time.monotonic() + 25
                while not window.evaluate_js("!!document.querySelector('.welcome') && document.querySelector('.chat-header h1')?.textContent === '新会话'"):
                    if time.monotonic() > deadline:
                        raise AssertionError('Production frontend did not open a blank session')
                    time.sleep(.1)
                sessions = app.store.sessions()
                assert len(sessions) == 2
                opened = next(s['id'] for s in sessions if s['id'] != historic)
                assert app.store.get(opened)['messages'] == []
                result['checks'].append('production-frontend-opens-blank-session')
            except Exception as error:
                result['error'] = repr(error)
            finally:
                window.destroy()

        try:
            webview.start(check, gui='edgechromium', private_mode=True, storage_path=str(root / 'profile'))
            # The real closed event owns cleanup; inspect before calling close again.
            assert app.closed
            assert {s['id'] for s in app.store.sessions()} == {historic}
            result['checks'].append('native-window-close-removes-unused-session')
            reopened = ModernApplication(root / 'modern', config, executor=NoExecution())
            try:
                assert reopened.bootstrap()['sessions'] == [original['session']]
                assert reopened.get_session(historic)['messages'] == original['messages']
                result['checks'].append('reopened-host-preserves-existing-dialogue-without-empty-history')
            finally:
                reopened.close()
            result['passed'] = 'error' not in result
        except Exception as error:
            result['error'] = repr(error)
        finally:
            app.close()
            assets.close()
    output = Path(__file__).resolve().parents[2] / 'installer' / 'verification' / 'empty-session-close-webview.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
