"""Real editor menu, non-contiguous IDs, US/UK audio, WebView and report import.

Uses a private Praat process/preferences/configuration; no model requests.
"""
import argparse
import ctypes
from contextlib import closing
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
U = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
U.EnumWindows.argtypes = [ENUM, W.LPARAM]
U.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
U.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
U.IsWindowEnabled.argtypes = [W.HWND]
U.IsHungAppWindow.argtypes = [W.HWND]
U.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
U.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
U.SetForegroundWindow.argtypes = [W.HWND]
U.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
U.SendMessageTimeoutW.restype = W.LPARAM
K = ctypes.windll.kernel32
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
K.CloseHandle.argtypes = [W.HANDLE]

PROBE = r'''
import json, os, time, threading
from pathlib import Path
import praat
import webview
from praat_ai.tutor import run_from_praat
from praat_ai.ui import TutorFormApplication
root = Path(os.environ['BRIDGE_PROBE_ROOT'])
original = webview.start
original_build = TutorFormApplication.build_request
def build(self, params):
    request = original_build(self, params)
    (root/'request.json').write_text(json.dumps(request.to_dict()), encoding='utf8')
    return request
TutorFormApplication.build_request = build
def check():
    window = webview.windows[-1]
    stopped = threading.Event()
    window.events.closed += stopped.set
    try:
        deadline = time.monotonic() + 30
        while not window.evaluate_js("!!document.querySelector('button[type=submit]')"):
            if time.monotonic() > deadline: raise RuntimeError('form timeout')
            time.sleep(.1)
        window.evaluate_js("(()=>{const e=document.querySelector('.pron-target select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,'en-US');e.dispatchEvent(new Event('change',{bubbles:true}));})()")
        window.evaluate_js("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='配置配套资源').click()")
        while not window.evaluate_js("!!document.querySelector('dialog[open]')"):
            if time.monotonic() > deadline: raise RuntimeError('resource editor timeout')
            time.sleep(.1)
        ui = window.evaluate_js("(()=>{const d=document.querySelector('dialog');const buttons=Array.from(d.querySelectorAll('button'));const a=buttons.find(e=>e.textContent==='检查 MFA').getBoundingClientRect();const b=buttons.find(e=>e.textContent==='检测对齐环境').getBoundingClientRect();return {g2pSelectors:Array.from(d.querySelectorAll('label')).filter(e=>e.textContent.includes('G2P 模型')).length,g2pImport:buttons.some(e=>e.textContent==='添加本地G2P模型'),declaration:d.textContent.includes('我确认所选资源'),gap:b.x-a.right};})()")
        assert ui['g2pSelectors'] == 0 and not ui['g2pImport'] and not ui['declaration'], ui
        assert ui['gap'] >= 12, ui
        window.evaluate_js("(()=>{const e=Array.from(document.querySelectorAll('dialog label')).find(e=>e.textContent==='Conda 环境名').querySelector('input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'unsaved-test');e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        window.evaluate_js("document.querySelector('button[aria-label=\"关闭资源配置\"]').click()")
        assert window.evaluate_js("!document.querySelector('dialog')"), 'X must close without a confirmation'
        (root/'ui-interaction.json').write_text(json.dumps(ui),encoding='utf8')
        window.evaluate_js("document.querySelector('.pron-switch').click()")
        window.evaluate_js("(()=>{const e=document.querySelector('.pron-ipa');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'h ə l oʊ');e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        window.evaluate_js("(()=>{const e=document.querySelector('.pron-fields input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'en');e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        assert window.evaluate_js("!document.querySelector('.pron-switch').checked")
        (root/'ready.json').write_text(json.dumps(dict(pid=os.getpid(),parentPid=os.environ.get('PRAAT_AI_PRAAT_PID'),selected=praat.get_selected())),encoding='utf8')
        while not (root/'action.txt').exists():
            if stopped.is_set(): return
            if time.monotonic() > deadline: raise RuntimeError('action timeout')
            time.sleep(.1)
        action=(root/'action.txt').read_text()
        if action=='cancel':
            window.evaluate_js("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='取消').click()")
        else:
            window.evaluate_js("document.querySelector('button[type=submit]').click()")
    except Exception as error:
        (root/'probe-error.txt').write_text(repr(error),encoding='utf8')
        window.destroy()
def start(callback,**options):
    threading.Thread(target=callback,daemon=True).start()
    return original(check,**options)
webview.start=start
code=run_from_praat()
(root/'done.json').write_text(json.dumps(dict(code=code)),encoding='utf8')
raise SystemExit(code)
'''

CHAT_PROBE = r'''
import json, os, threading, time
from pathlib import Path
import webview
from praat_ai.modern_host import main
root = Path(os.environ['BRIDGE_PROBE_ROOT'])
original = webview.start

def check():
    window = webview.windows[-1]
    try:
        def step(value):
            (root/'chat-report-step.txt').write_text(value, encoding='utf8')
        step('initial report')
        deadline = time.monotonic() + 30
        while not window.evaluate_js("!!document.querySelector('.report-figure img')?.complete && document.querySelector('.report-figure img')?.naturalWidth > 0"):
            if time.monotonic() > deadline: raise RuntimeError('chat report/image timeout')
            time.sleep(.1)
        body = window.evaluate_js('document.body.innerText')
        assert 'AI 纠音报告' in body and '具体结果' in body and '测量覆盖' in body, body
        assert 'En-uk-hello' in body and 'En-us-hello' in body, body
        assert '[外部图片已阻止' not in body
        data = window.evaluate_js("({title:document.querySelector('.chat-header h1').textContent,rows:document.querySelectorAll('.message table tbody tr').length,attachments:document.querySelectorAll('.attachment-chip').length,imageWidth:document.querySelector('.report-figure img').naturalWidth})")
        data['pid'] = os.getpid()
        assert data['rows'] > 0 and data['attachments'] == 3, data
        from PIL import ImageGrab
        import ctypes
        from ctypes import wintypes as W
        rect = W.RECT()
        ctypes.windll.user32.GetWindowRect(W.HWND(int(window.native.Handle.ToInt64())), ctypes.byref(rect))
        capture = Path(os.environ['BRIDGE_PROBE_CAPTURE'])
        time.sleep(.3)
        ImageGrab.grab((rect.left,rect.top,rect.right,rect.bottom)).save(capture.with_stem(capture.stem + '-summary'))
        window.evaluate_js("document.querySelector('.report-figure').scrollIntoView({block:'center'})")
        time.sleep(.3)
        ImageGrab.grab((rect.left,rect.top,rect.right,rect.bottom)).save(capture)
        data['screenshot'] = str(capture)
        step('captured report')
        application = window._js_api._application
        reports = [s for s in application.store.sessions() if s['title'].startswith('AI 纠音')]
        assert len(reports) == 1
        sid = reports[0]['id']
        message = application.store.get(sid)['messages'][0]
        from praat_ai.desktop_launch import write_record
        pending = application.report_runtime/'tutor-reports'/(sid + '.json')
        write_record(pending, dict(schema='tutor-chat-report-v1', source=message['report']['source'], files=[]))
        step('reopen request queued')
        while pending.exists():
            if time.monotonic() > deadline: raise RuntimeError('repeated report timeout')
            time.sleep(.1)
        assert len([s for s in application.store.sessions() if s['title'].startswith('AI 纠音')]) == 1
        assert len(application.store.get(sid)['messages']) == 1
        step('reopened once')
        window.evaluate_js("document.querySelector('button[aria-label=\"新建会话\"]').click()")
        while window.evaluate_js("!!document.querySelector('.report-figure')"):
            if time.monotonic() > deadline: raise RuntimeError('new conversation timeout')
            time.sleep(.1)
        window.evaluate_js("document.querySelector('button[aria-label=\"展开会话侧栏\"]')?.click()")
        window.evaluate_js(f"document.querySelector('[data-session-id=\"{sid}\"] .session-select').click()")
        step('history report selected')
        while not window.evaluate_js("!!document.querySelector('.report-figure img')?.naturalWidth"):
            if time.monotonic() > deadline: raise RuntimeError('report history revisit timeout')
            time.sleep(.1)
        assert 'AI 纠音报告' in window.evaluate_js('document.body.innerText')
        data['repeatedOpenAndHistoryRevisit'] = True
        (root/'chat-report-ready.json').write_text(json.dumps(data), encoding='utf8')
    except Exception as error:
        (root/'chat-report-error.txt').write_text(repr(error), encoding='utf8')
        window.destroy()

def start(callback, **options):
    threading.Thread(target=callback, daemon=True).start()
    return original(check, **options)
webview.start = start
raise SystemExit(main())
'''


def owned_windows(pid):
    result = []
    @ENUM
    def collect(hwnd, unused):
        owner = W.DWORD()
        U.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and U.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            U.GetWindowTextW(hwnd, title, len(title))
            result.append((int(hwnd), title.value))
        return True
    U.EnumWindows(collect, 0)
    return result


def wait(predicate, label, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Timed out: ' + label)


def pcm(path):
    assert path.read_bytes()[:4] == b'RIFF', path
    with wave.open(str(path)) as handle:
        assert handle.getsampwidth() == 2
        return handle.getnchannels(), handle.getframerate(), handle.getnframes(), handle.readframes(handle.getnframes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT/'ai/tests'))
    from verify_path_settings_live import command
    report = dict(checks=[], realCloud=False, executableSHA256=hashlib.sha256(args.exe.read_bytes()).hexdigest())
    source_dir = ROOT/'test-records'
    sources = [source_dir/'En-uk-hello.ogg', source_dir/'En-us-hello.ogg']
    original_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    process = None
    low = Path(os.environ['USERPROFILE'])/'AppData/LocalLow'
    with tempfile.TemporaryDirectory(prefix='praat-bridge-hello-', dir=low) as directory:
        root = Path(directory)
        ai = root/'ai'
        shutil.copytree(ROOT/'ai/praat_ai', ai/'praat_ai', ignore=shutil.ignore_patterns('__pycache__'))
        for file in (ROOT/'ai').glob('*.py'):
            shutil.copy2(file, ai/file.name)
        assets = ai/'frontend/dist'
        assets.mkdir(parents=True)
        shutil.copy2(ROOT/'ai/frontend/dist/index.html', assets/'index.html')
        shutil.copytree(ROOT/'ai/frontend/dist/assets', assets/'assets')
        (ai/'run_ai_tutor.py').write_text(PROBE, encoding='utf8')
        (ai/'run_ai_chat.py').write_text(CHAT_PROBE, encoding='utf8')
        config = ai/'ai_config.json'
        config.write_text(json.dumps(dict(api=dict(enabled=False), server=dict(auto_start=False),
            qwen=dict(model='offline-fixture'))), encoding='utf8')
        profile = root/'preferences'
        profile.mkdir()
        python = ROOT.parent/'venv-ai-modern/Scripts/python.exe'
        (profile/'Preferences.txt').write_text(f'Python.executablePath: {python}\nAI.projectDirectory: {ai}\n', encoding='utf8')
        quote = lambda path: str(path).replace('\u005c', '/').replace('"', '""')
        script = root/'prepare.praat'
        script.write_text('\n'.join([
            'Create Sound from formula: "discard", 1, 0, 0.1, 16000, ~0', 'discard = selected("Sound")',
            f'Read from file: "{quote(sources[0])}"', 'uk = selected("Sound")', 'Shift times by: 5',
            f'Save as WAV file: "{quote(root/"uk.wav")}"', 'To TextGrid: "phones", ""', 'grid = selected("TextGrid")',
            f'Read from file: "{quote(sources[1])}"', 'us = selected("Sound")', 'Shift times by: 7.5',
            f'Save as WAV file: "{quote(root/"us.wav")}"', 'selectObject: discard', 'Remove',
            'selectObject: uk', 'View & Edit', 'plusObject: grid', 'plusObject: us',
        ])+'\n', encoding='utf8')
        environment = {key:value for key,value in os.environ.items() if not key.startswith('PRAAT_')}
        environment.update(PRAAT_AI_PROJECT_DIR=str(ai), PRAAT_AI_CONFIG_PATH=str(config),
            PRAAT_PYTHON_EXECUTABLE=str(python), PRAAT_AI_REPORT_DIR=str(root/'reports'), BRIDGE_PROBE_ROOT=str(root),
            BRIDGE_PROBE_CAPTURE=str(args.output.resolve().with_name('tutor-chat-report-live.png')),
            TEMP=str(root), TMP=str(root))
        try:
            process = subprocess.Popen([str(args.exe.resolve()), '--new-send', '--no-plugins', '--no-pref-files', '--FULL-TRUST',
                '--pref-dir='+str(profile), str(script)], env=environment, cwd=root)
            editor = wait(lambda:next((window for window in owned_windows(process.pid)
                if 'En-uk-hello' in window[1]),None), 'hello editor')
            action = command(editor[0], ('Run AI tutor', 'AI 纠音', 'AI纠音'))
            for choice in ('cancel','submit'):
                for name in ('ready.json','action.txt','done.json','probe-error.txt'):
                    (root/name).unlink(missing_ok=True)
                assert U.PostMessageW(editor[0], 0x111, action, 0)
                wait(lambda:(root/'ready.json').exists(), 'real native-export WebView form')
                ready = json.loads((root/'ready.json').read_text(encoding='utf8'))
                report['resourceEditor'] = json.loads((root/'ui-interaction.json').read_text(encoding='utf8'))
                assert ready['parentPid'] == str(process.pid), ready
                selected = ready['selected']
                assert [row['id'] for row in selected] == [2,3,4], selected
                sounds = [row for row in selected if row['class']=='Sound']
                assert [row['id'] for row in sounds] == [2,4]
                assert pcm(Path(sounds[0]['file'])) == pcm(root/'uk.wav')
                assert pcm(Path(sounds[1]['file'])) == pcm(root/'us.wav')
                for sound, origin in zip(sounds, (5., 7.5)):
                    channels, rate, count, unused = pcm(Path(sound['file']))
                    assert sound['original_sound_domain'][0] == origin, sound
                    assert sound['wav_to_sound_offset_sec'] == origin, sound
                    assert abs(sound['original_sound_domain'][1]-origin-count/rate) <= 1/rate, sound
                    assert sound['sample_rate_hz'] == rate and sound['channels'] == channels, sound
                    assert sound['export_format'] == 'wav-pcm16', sound
                report['nativeExportSources'] = [{k: v for k, v in sound.items() if k != 'file'} for sound in sounds]
                assert Path(selected[1]['file']).suffix == '.TextGrid'
                assert 'TextGrid' in Path(selected[1]['file']).read_text(encoding='utf-8-sig')[:100]
                time.sleep(6)
                for hwnd, title in owned_windows(process.pid):
                    assert not U.IsHungAppWindow(hwnd), title
                    response = ctypes.c_size_t()
                    assert U.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 1000, ctypes.byref(response)), title
                    assert U.IsWindowEnabled(hwnd), ('native frame must remain movable', title)
                before = W.RECT()
                U.GetWindowRect(editor[0], ctypes.byref(before))
                U.SetForegroundWindow(editor[0])
                U.PostMessageW(editor[0], 0x112, 0xF010, 0)  # Native keyboard move loop.
                time.sleep(.2)
                for unused in range(3):
                    U.PostMessageW(editor[0], 0x100, 0x27, 1)
                    time.sleep(.05)
                U.PostMessageW(editor[0], 0x100, 0x0D, 1)
                time.sleep(.2)
                U.PostMessageW(editor[0], 0x1F, 0, 0)
                after = W.RECT()
                U.GetWindowRect(editor[0], ctypes.byref(after))
                assert after.left > before.left, ('editor move while tutor is open', before.left, after.left)
                report['checks'].append(choice+'-real-hello-PCM-identity-stable-IDs-selection-TextGrid-and-movable-responsive-parent')
                (root/'action.txt').write_text(choice, encoding='utf8')
                wait(lambda:(root/'done.json').exists(), 'helper finished')
                assert not (root/'probe-error.txt').exists()
                assert json.loads((root/'done.json').read_text())['code'] == 0
                wait(lambda:U.IsWindowEnabled(editor[0]), 'parent restored')
                if choice=='cancel':
                    assert not list((root/'reports').glob('*/ai_report.json'))
                    # An export write failure must stop before Python is launched.
                    blocked = Path(sounds[0]['file'])
                    assert blocked.parent.name == 'praat_py_workspace_'+str(process.pid)
                    blocked.unlink()
                    blocked.mkdir()
                    (root/'ready.json').unlink()
                    U.PostMessageW(editor[0],0x111,action,0)
                    message = wait(lambda:next((w for w in owned_windows(process.pid) if w[1]=='Message'),None), 'export error')
                    from verify_path_settings_live import children, text
                    detail = '\n'.join(text(child) for child in children(message[0]))
                    assert '无法为 Python 导出对象 2' in detail, detail
                    assert not (root/'ready.json').exists()
                    assert blocked.is_dir()
                    U.PostMessageW(message[0],0x10,0,0)
                    blocked.rmdir()
                    wait(lambda:all(w[1]!='Message' for w in owned_windows(process.pid)), 'export error dismissed')
                    report['checks'].append('export-failure-stops-before-launch-without-text-WAV-fallback-and-retry-restores-runner')
                else:
                    wait(lambda:(root/'chat-report-ready.json').exists() or (root/'chat-report-error.txt').exists(), 'actual conversation report')
                    assert not (root/'chat-report-error.txt').exists(), (root/'chat-report-error.txt').read_text() if (root/'chat-report-error.txt').exists() else ''
                    report['conversationReport'] = json.loads((root/'chat-report-ready.json').read_text(encoding='utf8'))
                    database = ai/'runtime/modern/sessions.sqlite3'
                    import sqlite3
                    with closing(sqlite3.connect(database)) as db:
                        messages = [json.loads(row[0]) for row in db.execute('SELECT payload FROM messages')]
                    assert any(m.get('report',{}).get('type') == 'pronunciation' for m in messages)
                    report['checks'].append('native-menu-analysis-opens-durable-conversation-report-with-measurements-and-inline-PNG')
            reports = list((root/'reports').glob('*/ai_report.json'))
            assert len(reports)==1, reports
            payload = json.loads(reports[0].read_text(encoding='utf8'))
            request = json.loads((root/'request.json').read_text())
            assert request['qwen_explain'] is False and request['qwen_vision'] is False
            assert request['reference_object']==2 and request['learner_object']==4
            assert payload['explanation'] == ''
            assert payload['sources']['reference']['wav_to_sound_offset_sec'] == 5., payload['sources']
            assert payload['sources']['learner']['wav_to_sound_offset_sec'] == 7.5, payload['sources']
            assert all(s['timeline_status'] == 'mapped' for s in payload['sources'].values())
            report['checks'].append('native-export-nonzero-Sound-domains-PCM-rate-channels-and-report-WAV-mapping')
            report['checks'].append('native-menu-submit-produced-real-hello-report-without-model-requests')
            # A separate, owned fixture verifies window teardown after parent exit.
            process.terminate()
            process.wait(timeout=5)
            desktop = K.OpenProcess(0x100000, False, report['conversationReport']['pid'])
            if desktop:
                try:
                    assert K.WaitForSingleObject(desktop, 15000) == 0, 'owned desktop must follow parent exit'
                finally:
                    K.CloseHandle(desktop)
            for name in ('ready.json','action.txt','done.json','probe-error.txt'):
                (root/name).unlink(missing_ok=True)
            process = subprocess.Popen([str(args.exe.resolve()), '--new-send', '--no-plugins', '--no-pref-files', '--FULL-TRUST',
                '--pref-dir='+str(profile), str(script)], env=environment, cwd=root)
            editor = wait(lambda:next((w for w in owned_windows(process.pid) if 'En-uk-hello' in w[1]),None),'second hello editor')
            action = command(editor[0], ('Run AI tutor','AI 纠音','AI纠音'))
            U.PostMessageW(editor[0],0x111,action,0)
            wait(lambda:(root/'ready.json').exists(),'parent-exit form')
            ready = json.loads((root/'ready.json').read_text())
            assert ready['parentPid'] == str(process.pid)
            child = K.OpenProcess(0x100000,False,ready['pid'])
            assert child
            try:
                process.terminate()
                process.wait(timeout=5)
                wait(lambda:(root/'done.json').exists(),'form followed native parent exit',timeout=8)
                assert K.WaitForSingleObject(child,5000)==0
                assert len(list((root/'reports').glob('*/ai_report.json')))==1
                report['checks'].append('native-parent-exit-closes-WebView-and-Python-child-without-new-analysis')
            finally:
                K.CloseHandle(child)
            assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources] == original_hashes
            report['checks'].append('original-US-UK-OGG-files-unchanged')
            report['passed'] = True
        except Exception as error:
            report.update(passed=False, error=repr(error), windows=owned_windows(process.pid) if process else [])
            from verify_path_settings_live import children, text
            report['dialogs'] = [[text(child) for child in children(hwnd)] for hwnd, title in report['windows'] if title == 'Message']
            if (root/'probe-error.txt').exists(): report['probeError']=(root/'probe-error.txt').read_text()
            if (root/'chat-report-error.txt').exists(): report['chatError']=(root/'chat-report-error.txt').read_text()
            if (root/'chat-report-step.txt').exists(): report['chatStep']=(root/'chat-report-step.txt').read_text()
            raise
        finally:
            # Only the isolated fixture process is closed; user Praat is untouched.
            if process and process.poll() is None:
                for hwnd, unused in owned_windows(process.pid): U.PostMessageW(hwnd,0x10,0,0)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
