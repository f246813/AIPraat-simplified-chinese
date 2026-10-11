"""Open the real WebView2 window from the newly built native menu."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ai'))
from praat_ai import sendpraat
from PIL import ImageGrab

spec=importlib.util.spec_from_file_location('menu_helpers',ROOT/'ai/tests/verify_path_settings_live.py')
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
fixture=ROOT/'.build-tools/anki-m4a-20261007/window-fixture'
home=fixture/'home';profile=home/'AppData/Roaming/Praat';profile.mkdir(parents=True,exist_ok=True)
(profile/'Preferences.txt').write_text('Python.executablePath: '+sys.executable+'\nAI.projectDirectory: '+str(ROOT/'ai')+'\n',encoding='utf-8')
script=fixture/'open.praat'
script.write_text('Create Sound from formula: "测试", 1, 0, 1, 44100, ~0.1*sin(2*pi*440*x)\nView & Edit\n',encoding='utf-8')
before={w.handle for w in sendpraat.list_windows()}
process=subprocess.Popen([str(ROOT/'Praat.exe'),'--new-send','--FULL-TRUST','--no-plugins',str(script)],env=dict(os.environ,USERPROFILE=str(home),APPDATA=str(profile.parent)))
window=None
try:
    objects=helpers.wait(lambda:next((w for w in sendpraat.praat_windows() if w.process_id==process.pid and w.title=='Praat Objects'),None),'objects')
    editor=helpers.wait(lambda:next((w for w in sendpraat.praat_windows() if w.process_id==process.pid and 'Sound' in w.title),None),'editor')
    time.sleep(.5)
    number=helpers.command(objects.handle,('Anki 连接','Anki integration'))
    editor_number=helpers.command(editor.handle,('Anki 连接','Anki integration'))
    helpers.press(objects.handle,number)
    window=helpers.wait(lambda:next((w for w in sendpraat.list_windows() if w.handle not in before and w.title=='Anki 连接 · AIPraat'),None),'Anki WebView2',timeout=25)
    time.sleep(4)
    import ctypes
    from ctypes import wintypes
    rect=wintypes.RECT();helpers.user32.GetWindowRect(window.handle,ctypes.byref(rect))
    ImageGrab.grab((rect.left,rect.top,rect.right,rect.bottom)).save(ROOT/'installer/verification/anki-window-live.png')
    report=dict(native_pid=process.pid,window_pid=window.process_id,objects_menu=number,editor_menu=editor_number,title=window.title)
    (ROOT/'installer/verification/anki-window-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
finally:
    if window:helpers.user32.PostMessageW(window.handle,0x10,0,0)
    process.terminate();process.wait(timeout=10)
