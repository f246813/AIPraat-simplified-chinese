"""Real React + WebView2 acceptance, isolated data and explicitly MOCK execution.

Run: python ai/tests/verify_modern_desktop.py --output <scratch>/desktop-result.json
No real model, Praat, user configuration, or old user database is accessed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from praat_ai.conversation_store import ConversationStore
from praat_ai.modern_app import HostAPI, ModernApplication
from praat_ai.modern_host import create_window


class MockExecutor:
    def __init__(self):
        self.calls = []
        self.release = threading.Event()
    def capture_target(self, text):
        return 'MOCK-original-target:' + text
    def run(self, **values):
        self.calls.append(values)
        text, emit, cancel = values['text'], values['emit'], values['cancel']
        emit('activity', dict(id='mock', type='tool', name='MOCK fixture (not Praat)',
                             status='running', execution='not_delivered', args={'target':values['target']}))
        emit('delta', dict(text='MOCK 流式正文：' + text))
        if text in {'任务A', '任务B'}:
            for _ in range(1200):
                if cancel.is_set() or self.release.wait(.05): break
        emit('activity', dict(id='mock', type='tool', name='MOCK fixture (not Praat)',
                             status='success', execution='not_delivered', result={'source':'mock-only'}))
        return dict(content='MOCK 完成：' + text, status='complete',
                    target=values['target'], evidence=[{'source':'mock-only', 'tool':'fixture'}],
                    attempts=[{'execution':'not_delivered'}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--followups-only', action='store_true', help='只验收旧快捷动作不显示、普通文字追问仍可提交')
    args = parser.parse_args()
    import webview
    # Remove inherited model/config overrides in this test process only.
    for key in list(os.environ):
        if key.startswith('PRAAT_AI_'): os.environ.pop(key)
    result = {'checks':[], 'execution':'MOCK only', 'realCloud':False, 'realPraat':False}
    with tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR')) as temporary:
        root = Path(temporary)
        config = root/'config.json'
        config.write_text(json.dumps({'api':{'enabled':True,'locked':False,'base_url':'http://127.0.0.1:8999/v1',
            'model':'mock-desktop','api_key':'fixture-key-never-returned','token_mode':'manual',
            'max_context_tokens':32768,'plan_max_tokens':1000,'stop_local_service':False},
            'qwen':{'model':'untouched-local'}, 'custom':{'preserve':'yes'}}), encoding='utf8')
        legacy = root/'legacy.sqlite3'
        old = ConversationStore(legacy)
        old_id = old.new_session()
        old.append(old_id,'user',{'prompt':'旧记录夹具'})
        old.append(old_id,'turn',{'report':'旧内容仅供查看','status':'complete'})
        legacy_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
        executor = MockExecutor()
        application = ModernApplication(root/'modern', config, legacy, executor=executor)
        first = application.store.new_session('会话A')
        second = application.store.new_session('会话B')
        application.store.put_message(first['id'], {'id':'fixture-history','role':'assistant','status':'complete',
            'taskId':'historical-task', 'followup_actions':['L2','L3','L4'],
            'content':'历史夹具（不是测量）\n\n公式 $x^2$\n\n```python\nprint(1)\n```\n\n```mermaid\ngraph TD; A-->B;\n```'})
        # A visible desktop is required: hidden WebView2 throttles animation frames.
        window, assets = create_window(application)
        applications = [application]
        def check():
            nonlocal application, executor
            def js(code): return window.evaluate_js(code)
            def wait(code, timeout=20):
                end = time.monotonic()+timeout
                while time.monotonic()<end:
                    if js(code): return
                    time.sleep(.1)
                raise AssertionError('Desktop condition timed out: '+code)
            def passed(name): result['checks'].append(name)
            def click(label):
                js(f"document.querySelector('[aria-label={json.dumps(label, ensure_ascii=False)}]').click()")
            def session(title):
                js(f"Array.from(document.querySelectorAll('.session-select')).find(e=>e.textContent.includes({json.dumps(title, ensure_ascii=False)})).click()")
                wait("document.querySelector('.chat-header h1')?.textContent.includes("+json.dumps(title,ensure_ascii=False)+")")
                wait("!!document.querySelector('[role=textbox][aria-label=消息输入]')")
            def text(value):
                js("document.querySelector('[role=textbox][aria-label=消息输入]').focus(); document.execCommand('selectAll'); document.execCommand('insertText',false,"+json.dumps(value,ensure_ascii=False)+")")
                wait("document.querySelector('[role=textbox][aria-label=消息输入]').textContent.includes("+json.dumps(value,ensure_ascii=False)+")")
                time.sleep(.15)
            try:
                wait("!!document.querySelector('[aria-label=展开会话侧栏]')")
                result['userAgent'] = js('navigator.userAgent')
                assert js('Object.keys(window.pywebview.api)') == ['rpc']
                assert 'fixture-key-never-returned' not in js('document.body.innerText')
                passed('real-WebView2-single-restricted-RPC-React-startup')
                wait("document.querySelector('.chat-header h1')?.textContent==='新会话'")
                initial_sessions = application.store.sessions()
                opened = next(s for s in initial_sessions if s['id'] not in {first['id'], second['id']} and not s['id'].startswith('legacy:'))
                assert application.store.get(opened['id'])['messages'] == []
                assert js("document.querySelectorAll('.message').length") == 0
                assert js("document.querySelector('[role=textbox][aria-label=消息输入]').textContent") == ''
                passed('opening-selects-blank-new-session-and-preserves-history')
                click('展开会话侧栏'); session('会话A')
                wait("!!document.querySelector('.katex') && !!document.querySelector('.hljs') && !!document.querySelector('.diagram svg')")
                passed('offline-Markdown-code-KaTeX-Mermaid')
                assert not js("Array.from(document.querySelectorAll('button')).some(e=>['解释结果','分析原材料','通用讨论'].includes(e.textContent.trim()))")
                passed('old-history-followup-actions-not-rendered')
                if args.followups_only:
                    for prompt in ('本次解释结果', '基于这些结果进行通用讨论'):
                        text(prompt); click('发送消息')
                        wait("document.body.innerText.includes("+json.dumps('MOCK 完成：'+prompt, ensure_ascii=False)+")")
                        assert executor.calls[-1]['text'] == prompt
                    assert not js("Array.from(document.querySelectorAll('button')).some(e=>['解释结果','分析原材料','通用讨论'].includes(e.textContent.trim()))")
                    passed('plain-typed-followup-submits-without-quick-actions')
                    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == legacy_hash
                    passed('legacy-database-byte-preserved')
                    result['passed'] = True
                    return
                text('任务A'); click('发送消息')
                wait("document.querySelectorAll('.message.user').length===1")
                session('会话B'); text('任务B'); click('发送消息')
                wait("!!document.querySelector('[aria-label=取消当前任务]')")
                wait("document.body.innerText.includes('MOCK 流式正文：任务B')")
                assert 'MOCK 流式正文：任务A' not in js('document.body.innerText')
                passed('background-events-stay-in-origin-session')
                session('会话A'); click('取消当前任务')
                wait("document.querySelector('.message.assistant:last-child')?.textContent.includes('已取消')")
                assert application.tasks[next(t for t,v in application.tasks.items() if v['sessionId']==second['id'])]['cancel'].is_set() is False
                executor.release.set()
                session('会话B'); wait("document.body.innerText.includes('MOCK 完成：任务B')")
                passed('cancellation-isolated-and-delivery-facts-preserved')
                click('打开设置')
                wait("!!document.querySelector('[aria-label=设置分类]')")
                assert js('document.querySelector(".settings-main h1").textContent') == '会话与存储'
                js('[...document.querySelectorAll(".settings-nav button")].find(e=>e.textContent.trim()==="模型").click()')
                wait("!!document.querySelector('input[type=password]')")
                assert js("document.querySelector('input[type=password]').value") == ''
                assert js("document.querySelectorAll('.test-result').length") == 2
                passed('settings-dashboard-and-credential-redaction')
                js('[...document.querySelectorAll(".settings-nav button")].find(e=>e.textContent.trim()==="会话与存储").click()')
                session_count = len(application.store.sessions())
                js('[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==="刷新宿主记录").click()')
                wait("document.body.innerText.includes('会话与任务状态已刷新')")
                assert len(application.store.sessions()) == session_count
                js('[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==="返回聊天").click()')
                wait("document.querySelector('.chat-header h1')?.textContent==='会话B'")
                passed('refresh-keeps-current-session-without-creating-another')
                # Swap only the test facade's private service, then reload the actual desktop.
                application.close()
                executor = MockExecutor()
                application = ModernApplication(root/'modern', config, legacy, executor=executor)
                application.window = window
                applications.append(application)
                api = window._js_api
                assert isinstance(api,HostAPI)
                api._application = application
                # Wait for a new document, not a matching element in the previous DOM.
                previous_origin = js('performance.timeOrigin')
                js('setTimeout(() => location.reload(), 50); true')
                wait(f'performance.timeOrigin !== {previous_origin}')
                wait("!!document.querySelector('[aria-label=展开会话侧栏]')")
                wait("document.querySelector('.chat-header h1')?.textContent==='新会话'")
                assert js("document.querySelectorAll('.message').length") == 0
                # Current defaults prune empty sessions on exit: assert identity
                # and history preservation, not the obsolete total-count formula.
                reopened = application.store.sessions()
                assert {first['id'], second['id']}.issubset({s['id'] for s in reopened})
                assert any(s['id'] not in {old['id'] for old in initial_sessions}
                           and s['title'] == '新会话' for s in reopened)
                passed('reopening-selects-another-blank-session')
                click('展开会话侧栏'); session('会话A')
                wait("document.body.innerText.includes('MOCK 完成：任务A')")
                assert not executor.calls
                passed('restart-restores-dialogue-without-execution-replay')
                text('继续C'); click('发送消息')
                wait("document.body.innerText.includes('MOCK 完成：继续C')")
                assert any('任务A' in str(m.get('content','')) for m in executor.calls[0]['history'])
                assert executor.calls[0]['target']=='MOCK-original-target:继续C'
                passed('resumed-turn-uses-history-and-new-bound-target')
                for prompt in ('本次解释结果', '基于这些结果进行通用讨论'):
                    text(prompt); click('发送消息')
                    wait("document.body.innerText.includes("+json.dumps('MOCK 完成：'+prompt, ensure_ascii=False)+")")
                    assert executor.calls[-1]['text'] == prompt
                assert not js("Array.from(document.querySelectorAll('button')).some(e=>['解释结果','分析原材料','通用讨论'].includes(e.textContent.trim()))")
                passed('plain-typed-followup-submits-without-quick-actions')
                js("Array.from(document.querySelectorAll('.session-select')).find(e=>e.textContent.includes('旧记录夹具')).click()")
                wait("!!document.querySelector('.readonly-footer')")
                assert not js("document.querySelector('[aria-label=消息输入]')!==null")
                assert hashlib.sha256(legacy.read_bytes()).hexdigest()==legacy_hash
                passed('legacy-records-read-only-and-byte-preserved')
                assert not js("document.querySelector('[role=alert]')!==null")
                resources=js("performance.getEntriesByType('resource').map(r=>r.name)")
                assert all(name.startswith(assets.url.rsplit('/',1)[0]) or name.startswith('data:') for name in resources)
                passed('all-page-resources-loopback-or-data')
                result['assetRequests']=len(resources)
                result['passed']=True
            except Exception as error:
                result['passed']=False; result['error']=repr(error)
                result['dom']=js('document.body.innerText.slice(0,6000)')
            finally:
                for app in applications: app.close()
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
                window.destroy()
        try:
            webview.start(check, gui='edgechromium', private_mode=True, storage_path=str(root/'profile'))
        finally:
            for app in applications: app.close()
            assets.close()
    print(args.output.read_text(encoding='utf8'))
    return 0 if result.get('passed') else 1


if __name__=='__main__': raise SystemExit(main())
