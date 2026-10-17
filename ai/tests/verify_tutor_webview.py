"""Real WebView2 form + existing offline pronunciation pipeline; fixture audio only."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import sys
import tempfile
import time
import types
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("submit", "cancel", "close"), default="submit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import numpy as np
    import webview
    from praat_ai.tutor import run_from_praat
    from PIL import ImageGrab
    result = dict(action=args.action, realCloud=False, checks=[])
    for key in list(os.environ):
        if key.startswith("PRAAT_AI_"):
            os.environ.pop(key)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.json"
        config.write_text(json.dumps(dict(qwen=dict(model="local-fixture"), api=dict(enabled=False),
            server=dict(auto_start=False), alignment=dict(backend="dtw"))), encoding="utf8")
        output = root / "output"
        output.mkdir()
        for name, hz in [("standard", 220), ("learner", 240)]:
            samples = (np.sin(2 * np.pi * hz * np.arange(16000) / 16000) * 10000).astype("<i2")
            with wave.open(str(root / f"{name}.wav"), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
                stream.writeframes(samples.tobytes())
        helper = types.SimpleNamespace(get_selected=lambda: [
            dict(id=4, name="标准音_hello", **{"class":"Sound"}, file=str(root / "standard.wav")),
            dict(id=9, name="学习者_hello", **{"class":"Sound"}, file=str(root / "learner.wav")),
        ], get_output_dir=lambda: str(output))
        original_start = webview.start

        def check():
            window = webview.windows[-1]
            def wait(code):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if window.evaluate_js(code):
                        return
                    time.sleep(.1)
                raise AssertionError("Timed out: " + code)
            try:
                wait("document.body.innerText.includes('当前模型：本地模型 · local-fixture')")
                assert window.evaluate_js("Object.keys(window.pywebview.api)") == ["rpc"]
                assert "Edg" in window.evaluate_js("navigator.userAgent")
                result["checks"].append("production-React-assets-real-WebView2-RPC-and-local-model-label")
                if args.action == "close":
                    window.destroy()
                    return
                if args.action == "cancel":
                    window.evaluate_js("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='取消').click()")
                    return
                # Both explanation switches are off: this probe must not contact any model.
                window.evaluate_js("document.querySelector('.pron-switch').click()")
                window.evaluate_js("document.querySelector('summary').click()")
                wait("document.querySelector('details').open")
                assert window.evaluate_js("!document.querySelectorAll('.pron-switch')[1].checked")
                window.evaluate_js("document.querySelector('.pron-layout').scrollTop=0")
                time.sleep(.2)
                rect = ctypes.wintypes.RECT()
                hwnd = int(window.native.Handle.ToInt64())
                ctypes.windll.user32.GetWindowRect(ctypes.wintypes.HWND(hwnd), ctypes.byref(rect))
                ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(args.output.with_suffix(".png"))
                window.evaluate_js("document.querySelector('button[type=submit]').click()")
            except Exception as error:
                result["error"] = repr(error)
                window.destroy()

        def start(callback, **options):
            return original_start(check, **options)

        with patch.dict(sys.modules, {"praat":helper}), patch("webview.start", side_effect=start), \
             patch.dict(os.environ, {"PRAAT_AI_REPORT_DIR":str(root / "reports")}):
            code = run_from_praat(config_path=config)
        result["exitCode"] = code
        reports = list((root / "reports").glob("*/ai_report.json"))
        result["reportsGenerated"] = len(reports)
        if args.action == "submit" and reports:
            payload = json.loads(reports[0].read_text(encoding="utf8"))
            result["checks"].append("request-reached-existing-offline-analysis-and-persistent-JSON-report")
            result["textgridGenerated"] = bool(list(output.glob("*.TextGrid")))
        elif args.action != "submit" and not reports:
            result["checks"].append("cancel-or-native-close-produces-no-analysis")
        result["passed"] = code == 0 and "error" not in result and (bool(reports) if args.action == "submit" else not reports)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
