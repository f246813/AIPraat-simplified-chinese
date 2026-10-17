"""Production WebView2 local-model path settings, isolated config and picker fixture."""
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webview
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window
from tests.test_modern_organization import NoExecution

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf8', errors='replace')
result = dict(productionAssets=True, realWebView2=True, isolatedData=True)
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    config = root / 'config.json'
    config.write_text('{}', encoding='utf8')
    model = root / '本地模型.gguf'
    model.write_bytes(b'GGUF')
    app = ModernApplication(root / 'modern', config, executor=NoExecution())
    window, assets = create_window(app, hidden=True)
    picker_calls = []
    def picker(*args, **kwargs):
        picker_calls.append(kwargs)
        return (str(model),) if len(picker_calls) == 1 else None
    window.create_file_dialog = picker
    def check():
        def js(code): return window.evaluate_js(code)
        def wait(code):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if js(code): return
                time.sleep(.1)
            raise AssertionError('Timed out: ' + code)
        def click(selector): js('document.querySelector(' + json.dumps(selector) + ').click()')
        try:
            wait('document.querySelector("[aria-label=打开设置]")')
            click('[aria-label="打开设置"]')
            js('[...document.querySelectorAll(".settings-nav button")].find(b=>b.textContent==="模型").click()')
            js('[...document.querySelectorAll(".segmented button")].find(b=>b.textContent==="本地模型").click()')
            wait('document.querySelector("[aria-label=添加本地模型]")')
            assert js('document.querySelector(".local-model-actions").parentElement.lastElementChild.className') == 'local-model-actions'
            before = config.read_bytes()
            click('[aria-label="添加本地模型"]')
            wait('document.querySelector(".local-model-file")?.title===' + json.dumps(str(model)))
            assert config.read_bytes() == before
            js('[...document.querySelectorAll(".settings-page-actions button")].find(b=>b.textContent==="保存更改").click()')
            wait('document.querySelector(".notice")?.textContent.includes("设置已保存")')
            saved = app.settings.get()
            assert saved['server']['model_path'] == str(model)
            assert saved['local']['model'] == model.name
            assert not saved['api']['enabled']
            assert app.local.manager is None and not app.tasks
            after = config.read_bytes()
            click('[aria-label="添加本地模型"]')
            wait('!document.querySelector("[aria-label=添加本地模型]").disabled')
            assert js('document.querySelector(".local-model-file").title') == str(model)
            assert config.read_bytes() == after
            assert len(picker_calls) == 2
            assert all(not call['allow_multiple'] and '*.gguf' in call['file_types'][0] for call in picker_calls)
            result.update(passed=True, pickerSaveCancel=True, noModelStarted=True, persistedLocalModel=True)
        except Exception as error:
            result.update(passed=False, error=repr(error), page=js('document.body.innerText'))
        finally:
            window.destroy()
    try:
        webview.start(check, gui='edgechromium', private_mode=True, storage_path=str(root / 'profile'))
    finally:
        app.close()
        assets.close()
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result.get('passed') else 1)
