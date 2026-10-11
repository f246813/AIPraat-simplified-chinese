"""Real WebView2 dictionary interaction with isolated files and configuration."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from praat_ai.speech_dictionaries import DictionaryApplication
from praat_ai.modern_app import HostAPI
from praat_ai.modern_host import AssetServer


def main():
    import webview
    project = Path(__file__).resolve().parents[2]
    output = project / 'test-records' / 'installer' / 'speech-dictionaries-live.json'
    report = {'checks': [], 'errors': []}
    with tempfile.TemporaryDirectory(prefix='praat-dictionaries-live-') as directory:
        root = Path(directory)
        first, second = root / '中文语音词典.dict', root / '英语词典.txt'
        first_model, second_model = root / '中文声学.zip', root / '英语声学.zip'
        first_wav, second_wav = root / '中文Wav2Vec2', root / '英语Wav2Vec2'
        first_wav.mkdir(); second_wav.mkdir()
        for path in (first, second): path.write_text('hello HH AH L OW\n', encoding='utf-8')
        for path in (first_model, second_model): path.write_bytes(b'fixture-model')
        config = root / 'config.json'
        config.write_text(json.dumps({'alignment': {'mfa': {'dictionary_path': str(first), 'acoustic_model': str(first_model)},
                                      'wav2vec2': {'model': str(first_wav), 'enabled': True, 'device': 'cpu'}},
                                      'custom': {'keep': True}}), encoding='utf-8')
        app = DictionaryApplication(config)
        server = AssetServer(project / 'ai' / 'frontend' / 'dist')
        api = HostAPI(app)
        window = webview.create_window('词典与模型管理交互验收', server.url + '?window=dictionaries',
            js_api=api, width=780, height=480, min_size=(620,360))
        app.window = window
        native_picker = window.create_file_dialog
        api._origin = server.url.rsplit('/', 1)[0]

        def verify():
            def js(source): return window.evaluate_js(source)
            def wait(source):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if js(source): return
                    time.sleep(.1)
                raise AssertionError('Timed out: ' + source)
            def passed(name):
                report['checks'].append(name)
                print('PASS ' + name, flush=True)
            try:
                wait("document.querySelectorAll('.dictionary-row').length===1")
                assert js('Object.keys(window.pywebview.api)') == ['rpc']
                assert '中文语音词典.dict' in js('document.body.innerText')
                passed('real_WebView2_legacy_dictionary_and_restricted_RPC')
                # Open and cancel the real Windows native file picker.
                before = config.read_bytes()
                js("document.querySelector('.dictionary-window>footer button').click()")
                user32 = ctypes.windll.user32
                callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                user32.PostMessageW.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
                user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
                user32.GetClassNameW.argtypes = [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
                picker = []
                @callback
                def find(hwnd, _):
                    pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    name = ctypes.create_unicode_buffer(256); user32.GetClassNameW(hwnd,name,256)
                    if pid.value == os.getpid() and name.value == '#32770' and user32.IsWindowVisible(hwnd):
                        picker.append(hwnd)
                    return True
                deadline = time.monotonic() + 15
                while not picker and time.monotonic() < deadline:
                    user32.EnumWindows(find, 0); time.sleep(.1)
                assert picker, 'Native file picker did not open'
                user32.PostMessageW(picker[0], 0x10, 0, 0)
                wait("!document.querySelector('.dictionary-window>footer button').disabled")
                assert config.read_bytes() == before
                passed('native_Windows_file_picker_cancel_preserves_config')
                # Supply a fixture path through the same chooser RPC after the real picker check.
                window.create_file_dialog = lambda *args, **kwargs: [str(second)]
                js("document.querySelector('.dictionary-window>footer button').click()")
                wait("document.querySelectorAll('.dictionary-row').length===2")
                passed('chooser_RPC_adds_Unicode_path_and_refreshes_list')
                js("Array.from(document.querySelectorAll('.dictionary-row')).find(e=>e.textContent.includes('英语词典.txt')).dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:140,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=select]:not(:disabled)')")
                js("document.querySelector('[data-context-menu-item=select]').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('英语词典.txt')")
                assert app._service.list()['dictionaries'][0]['path'] == str(second)
                assert js("document.querySelectorAll('.dictionary-row.is-active').length") == 1
                assert js("!!document.querySelector('.dictionary-row.is-active .dictionary-current svg')")
                # The assistant button theme animates border-color for 150 ms;
                # Windows display scaling can snap a CSS 1px border to 0.8px.
                wait("(()=>{const row=document.querySelector('.dictionary-row.is-active');return getComputedStyle(row).borderTopColor===getComputedStyle(row.querySelector('.dictionary-current svg')).color})()")
                colors = js("(()=>{const row=document.querySelector('.dictionary-row.is-active');return {border:getComputedStyle(row).borderTopColor,check:getComputedStyle(row.querySelector('.dictionary-current svg')).color,width:getComputedStyle(row).borderTopWidth}})()")
                assert colors['border'] == colors['check'] and float(colors['width'].removesuffix('px')) > 0, colors
                red, green, blue = [int(channel) for channel in colors['check'].removeprefix('rgb(').removesuffix(')').split(',')]
                assert green > red and green > blue, colors
                passed('select_dictionary_RPC_updates_MFA_path_and_green_check_border')
                # Select the original again to keep the existing delete-current regression.
                js("Array.from(document.querySelectorAll('.dictionary-row')).find(e=>e.textContent.includes('中文语音词典.dict')).dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:140,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=select]:not(:disabled)')")
                js("document.querySelector('[data-context-menu-item=select]').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('中文语音词典.dict')")
                js("document.querySelector('.dictionary-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:140,clientY:140}))")
                wait("!!document.querySelector('[role=menuitem]')")
                js("document.querySelector('[data-context-menu-item=remove]').click()")
                wait("!!document.querySelector('dialog[open]')")
                js("Array.from(document.querySelectorAll('dialog button')).find(e=>e.textContent==='取消').click()")
                assert len(app._service.list()['dictionaries']) == 2
                passed('right_click_delete_cancel_preserves_dictionary')
                js("document.querySelector('.dictionary-row').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:140,clientY:140}))")
                wait("!!document.querySelector('[role=menuitem]')")
                js("document.querySelector('[data-context-menu-item=remove]').click()")
                wait("!!document.querySelector('dialog[open]')")
                js("Array.from(document.querySelectorAll('dialog button')).find(e=>e.textContent==='删除').click()")
                wait("document.querySelectorAll('.dictionary-row').length===1 && !document.querySelector('dialog')")
                assert first.is_file() and second.is_file()
                assert app._service.list()['dictionaries'][0]['active']
                assert json.loads(config.read_text(encoding='utf-8'))['custom']['keep']
                passed('confirmed_delete_keeps_disk_file_and_selects_remaining_dictionary')
                assert js("document.querySelector('.dictionary-list').getBoundingClientRect().bottom < document.querySelector('.dictionary-window>footer').getBoundingClientRect().top")
                passed('bottom_add_action_remains_below_scrollable_list')
                js("Array.from(document.querySelectorAll('.dictionary-nav button')).find(e=>e.textContent==='MFA声学模型').click()")
                wait("document.querySelector('.dictionary-row')?.textContent.includes('中文声学.zip')")
                assert js("document.querySelector('h1').textContent") == '管理MFA声学模型'
                assert js("document.querySelector('.dictionary-nav button[aria-current=page]').textContent") == 'MFA声学模型'
                passed('sidebar_switches_to_acoustic_models_and_retains_legacy_model')
                window.create_file_dialog = lambda *args, **kwargs: [str(second_model)]
                js("document.querySelector('.dictionary-window>footer button').click()")
                wait("document.querySelectorAll('.dictionary-row').length===2")
                js("Array.from(document.querySelectorAll('.dictionary-row')).find(e=>e.textContent.includes('英语声学.zip')).dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:250,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=select]:not(:disabled)')")
                js("document.querySelector('[data-context-menu-item=select]').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('英语声学.zip')")
                saved = json.loads(config.read_text(encoding='utf-8'))['alignment']['mfa']
                assert saved['acoustic_model'] == str(second_model) and saved['dictionary_path'] == str(second)
                passed('model_picker_select_persists_MFA_model_without_changing_dictionary')
                js("document.querySelector('.dictionary-row.is-active').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:250,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=remove]')")
                js("document.querySelector('[data-context-menu-item=remove]').click()")
                wait("!!document.querySelector('dialog[open]')")
                js("Array.from(document.querySelectorAll('dialog button')).find(e=>e.textContent==='删除').click()")
                wait("document.querySelectorAll('.dictionary-row').length===1 && !document.querySelector('dialog')")
                assert app._models.list()['models'][0]['active'] and first_model.is_file() and second_model.is_file()
                js("Array.from(document.querySelectorAll('.dictionary-nav button')).find(e=>e.textContent==='MFA语音词典').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('英语词典.txt')")
                passed('model_delete_keeps_files_and_dictionary_sidebar_restores_current_selection')
                js("Array.from(document.querySelectorAll('.dictionary-nav button')).find(e=>e.textContent==='Wav2Vec2模型').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('中文Wav2Vec2')")
                assert js("document.querySelector('h1').textContent") == '管理Wav2Vec2声学模型'
                passed('Wav2Vec2_sidebar_backfills_existing_model_directory')
                window.create_file_dialog = native_picker
                picker.clear()
                before = config.read_bytes()
                js("document.querySelector('.dictionary-window>footer button').click()")
                deadline = time.monotonic() + 15
                while not picker and time.monotonic() < deadline:
                    user32.EnumWindows(find, 0); time.sleep(.1)
                assert picker, 'Native folder picker did not open'
                user32.PostMessageW(picker[0], 0x10, 0, 0)
                wait("!document.querySelector('.dictionary-window>footer button').disabled")
                assert config.read_bytes() == before
                passed('native_Wav2Vec2_folder_picker_cancel_preserves_config')
                window.create_file_dialog = lambda *args, **kwargs: [str(second_wav)]
                js("document.querySelector('.dictionary-window>footer button').click()")
                wait("document.querySelectorAll('.dictionary-row').length===2")
                js("Array.from(document.querySelectorAll('.dictionary-row')).find(e=>e.textContent.includes('英语Wav2Vec2')).dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:250,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=select]:not(:disabled)')")
                js("document.querySelector('[data-context-menu-item=select]').click()")
                wait("document.querySelector('.dictionary-row.is-active')?.textContent.includes('英语Wav2Vec2')")
                saved = json.loads(config.read_text(encoding='utf-8'))['alignment']
                assert saved['wav2vec2']['model'] == str(second_wav)
                assert saved['wav2vec2']['enabled'] is True and saved['wav2vec2']['device'] == 'cpu'
                assert saved['mfa']['dictionary_path'] == str(second) and saved['mfa']['acoustic_model'] == str(first_model)
                js("document.querySelector('.dictionary-row.is-active').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:250,clientY:140}))")
                wait("!!document.querySelector('[data-context-menu-item=remove]')")
                js("document.querySelector('[data-context-menu-item=remove]').click()")
                wait("!!document.querySelector('dialog[open]')")
                js("Array.from(document.querySelectorAll('dialog button')).find(e=>e.textContent==='删除').click()")
                wait("document.querySelectorAll('.dictionary-row').length===1 && !document.querySelector('dialog')")
                assert app._wav2vec2.list()['models'][0]['active'] and first_wav.is_dir() and second_wav.is_dir()
                passed('Wav2Vec2_add_select_delete_retains_directories_and_runtime_parameters')
            except Exception as error:
                report['errors'].append(str(error))
                print('FAIL ' + str(error), flush=True)
            finally:
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                window.destroy()
        try:
            webview.start(verify, gui='edgechromium', private_mode=True)
        finally:
            server.close()
    return bool(report['errors'])


if __name__ == '__main__':
    raise SystemExit(main())
