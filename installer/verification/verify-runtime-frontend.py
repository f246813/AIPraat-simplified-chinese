"""Launch the actual bundled React frontend and verify its WebView2 JS bridge."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"ai"))
import webview
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window

success=[]
failure=[]
with tempfile.TemporaryDirectory(prefix="AIPraat-frontend-acceptance-") as directory:
    root=Path(directory)
    config=root/"ai_config.json"
    config.write_text(json.dumps({"api":{"enabled":False},"server":{"auto_start":False}}),encoding="utf-8")
    app=ModernApplication(root/"modern",config,root/"legacy.sqlite3",allow_cloud=False,configured_cloud=False)
    window,server=create_window(app)
    def check():
        try:
            for _ in range(80):
                state=window.evaluate_js("({rendered:document.getElementById('root')?.children.length>0, bridge:typeof window.pywebview?.api?.rpc === 'function', text:document.body.innerText.length})")
                if state.get("rendered") and state.get("bridge") and state.get("text",0)>30:
                    success.append(state)
                    break
                time.sleep(.25)
            if not success:
                failure.append("React or native RPC bridge did not become ready")
        except Exception as error:
            failure.append(str(error))
        finally:
            window.destroy()
    window.events.loaded+=check
    # A watchdog prevents a renderer failure from leaving the acceptance window open.
    timer=threading.Timer(35,window.destroy)
    timer.start()
    try:
        webview.start(gui="edgechromium",private_mode=True,storage_path=str(root/"webview"))
    finally:
        timer.cancel();app.close();server.close()
    assert success and not failure,failure
    print("PASS actual AIPraat React frontend / WebView2 / pythonnet RPC bridge",success[0])
