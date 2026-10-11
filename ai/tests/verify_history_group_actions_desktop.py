"""Production WebView2 history menus using isolated stores and no execution."""
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import webview
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window
from praat_ai import modern_budget

class NoExecution:
    def capture_target(self,text):raise AssertionError('Unexpected Praat capture')
    def run(self,**kwargs):raise AssertionError('Unexpected execution')

output=Path(__file__).resolve().parents[2]/'test-records/installer'
result=dict(realWebView2=True,productionAssets=True,checks=[])
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);config=root/'config.json';config.write_text('{}',encoding='utf8')
    legacy=root/'legacy.sqlite3'
    with closing(sqlite3.connect(legacy)) as db,db:
        db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,created TEXT);CREATE TABLE events(id INTEGER PRIMARY KEY,session TEXT,kind TEXT,payload TEXT);')
        db.execute('INSERT INTO sessions VALUES (?,?)',('old',datetime.now(timezone.utc).isoformat()))
        db.execute('INSERT INTO events VALUES (?,?,?,?)',(1,'old','user',json.dumps(dict(prompt='旧记录验收'))))
    original=legacy.read_bytes()
    app=ModernApplication(root/'modern',config,legacy,executor=NoExecution())
    detached=app.store.new_session('保留内容')['id']
    with app.store.connect() as db:db.execute('UPDATE sessions SET project_path=NULL WHERE id=?',(detached,))
    section=app.store.organization.create('其他分区')['id']
    keep=app.store.organization.create_session('其他分区保留',section)['id']
    window,assets=create_window(app)

    def check():
        def js(code):return window.evaluate_js(code)
        def wait(code):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if js(code):return
                time.sleep(.1)
            raise AssertionError('Timed out: '+code)
        def click(selector):js('document.querySelector('+json.dumps(selector)+').click()')
        def text_button(label,role='button'):
            js('Array.from(document.querySelectorAll('+json.dumps('[role="menuitem"]' if role=='menuitem' else 'button')+')).find(el=>el.textContent.trim()==='+json.dumps(label)+').click()')
        def context(selector):
            js('(()=>{const el=document.querySelector('+json.dumps(selector)+'),r=el.getBoundingClientRect();el.dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,cancelable:true,clientX:r.left+20,clientY:r.top+10}));})()')
            wait('document.querySelector("[role=menu]")')
        def exists(selector):return 'document.querySelector('+json.dumps(selector)+')'
        def passed(name):result['checks'].append(name)
        try:
            wait('document.querySelector("[aria-label=展开会话侧栏]")');click('[aria-label=展开会话侧栏]')
            wait(exists('[data-session-id="legacy:old"]'))
            context('.sidebar-origin-group:has([data-session-id="legacy:old"]) .sidebar-time-group-header')
            assert js('Array.from(document.querySelectorAll("[role=menuitem]")).map(el=>el.textContent)')==['仅删除分区','删除分区内所有内容','全部归档','删除分区及其内容']
            text_button('仅删除分区','menuitem');wait('document.querySelector("[aria-label=确认仅删除分区]")');click('[aria-label=确认仅删除分区]')
            wait('!'+exists('dialog.modal[open]'))
            assert app.store.get(detached)['session']['timeGroupDetached']
            assert app.store.get('legacy:old')['session']['timeGroupDetached']
            passed('detach-group-retains-modern-and-readonly-content')
            opened=next(s['id'] for s in app.store.sessions() if s['title']=='新会话')
            context('.sidebar-origin-group:has([data-session-id='+json.dumps(opened)+']) .sidebar-time-group-header')
            text_button('全部归档','menuitem');wait('document.querySelector("[aria-label=确认全部归档]")');click('[aria-label=确认全部归档]')
            wait('!'+exists('[data-session-id="'+opened+'"]'))
            assert app.store.get(opened)['session']['archived']
            passed('archive-group-excludes-other-origins-and-custom-sections')
            click('[aria-label=打开设置]');text_button('已归档会话');wait(exists('[aria-label="取消归档 新会话"]'));click('[aria-label="取消归档 新会话"]');text_button('返回聊天')
            wait(exists('[data-session-id="'+opened+'"]'))
            passed('archived-group-content-restores-through-existing-page')
            wait(exists('[data-session-id="legacy:old"]'))
            context('[data-session-id="legacy:old"]')
            text_button('删除','menuitem')
            wait(exists('dialog.modal[aria-label="删除会话"][open]'))
            click('dialog.modal[aria-label="删除会话"] button.danger')
            wait('!'+exists('[data-session-id="legacy:old"]'))
            passed('readonly-session-deletes-through-existing-confirmation')
            context('.sidebar-origin-group:has([data-session-id='+json.dumps(opened)+']) .sidebar-time-group-header')
            text_button('删除分区内所有内容','menuitem');wait('document.querySelector("[aria-label=确认删除分区内所有内容]")');click('[aria-label=确认删除分区内所有内容]')
            wait('!'+exists('[data-session-id="'+opened+'"]'))
            assert {s['id'] for s in app.store.sessions()}=={detached,keep}
            assert legacy.read_bytes()==original
            assert not app.tasks
            passed('clear-group-only-target-members-and-preserves-legacy-source')
            result.update(passed=True,userAgent=js('navigator.userAgent'))
        except Exception as error:result.update(passed=False,error=repr(error),page=js('document.body.innerText'))
        finally:window.destroy()
    with patch.object(modern_budget,'text_request',side_effect=AssertionError('Unexpected model request')) as request:
        try:
            webview.start(check,gui='edgechromium',private_mode=True,storage_path=str(root/'profile'))
            assert request.call_count==0
        finally:app.close();assets.close()
(output/'history-group-actions-webview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if result.get('passed') else 1)
