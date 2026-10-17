"""Offline high-risk service checks; never real provider or user database."""
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai.config import AppConfig, QwenConfig
from praat_ai.conversation_store import ConversationStore
from praat_ai.modern_app import ModernApplication, HostAPI, LocalResources
from praat_ai.modern_store import ModernStore
from praat_ai.modern_settings import SettingsService, assert_endpoint
from praat_ai import modern_budget as budget, qwen


class FakeExecutor:
    def __init__(self):
        self.started = {}
        self.gates = {}
        self.calls = []
    def capture_target(self, text):
        return 'original-target:' + text
    def run(self, **kwargs):
        text = kwargs['text']
        gate = self.gates.setdefault(text, threading.Event())
        self.calls.append(kwargs)
        self.started.setdefault(text, threading.Event()).set()
        kwargs['emit']('activity', dict(id='tool', type='tool', name='duration', args={}, result=['15 ms'], status='success', execution='delivered'))
        kwargs['emit']('delta', dict(text=text + '-stream'))
        for _ in range(200):
            if gate.wait(.01) or kwargs['cancel'].is_set(): break
        return dict(content=text + '-result', status='complete', evidence=[dict(tool='duration', rows=['15 ms'], source=kwargs['target'])],
                    attempts=[dict(execution='delivered')], target=kwargs['target'])


class ModernTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        for k in list(os.environ):
            if k.startswith('PRAAT_AI_'): os.environ.pop(k)
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(dict(api=dict(enabled=True, locked=False, base_url='http://127.0.0.1:8999/v1', model='mock', api_key='private-api-secret',
                                                       max_context_tokens=32768, plan_max_tokens=1000, token_mode='manual', stop_local_service=False),
                                               qwen=dict(model='local-user-model', plan_temperature=.7, top_p=.93, presence_penalty=.2, api_key='local-secret'),
                                               custom=dict(preserve='yes'))), encoding='utf8')
        self.fake = FakeExecutor()
        self.app = ModernApplication(self.root / 'new', self.config, executor=self.fake)
    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()
        self.env.stop()
    def wait(self, condition):
        deadline = time.monotonic() + 4
        while not condition():
            if time.monotonic() > deadline: self.fail('task timeout')
            time.sleep(.01)
    def session(self): return self.app.rpc('sessions.create', {})['id']

    def test_session_pin_additive_persistence_order_and_preserved_data(self):
        created = self.app.rpc('sessions.create', dict(title='historic'))
        self.assertIs(created['pinned'], False)
        a, b, c = created['id'], self.session(), self.session()
        store = self.app.store
        store.put_message(a, dict(id='historic', role='user', content='original history', status='complete'))
        history, _ = store.context(a)
        store.summarize(a, 'original summary', history[-1]['_seq'])
        store.put_message(a, dict(id='recent', role='assistant', content='recent answer', status='complete'))
        store.save_evidence(a, 'original-task', dict(rows=['15 ms'], source='original target', execution='delivered'))
        store.view(a, draft='saved draft', scroll=321)
        with store.connect() as db:
            for sid, stamp in ((a, '2020-01-01'), (b, '2021-01-01'), (c, '2022-01-01')):
                db.execute('UPDATE sessions SET updated=? WHERE id=?', (stamp, sid))
            # Simulate upgrading an existing store, not replacing its data/schema.
            db.execute('DROP TABLE session_pins')
            schemas = [tuple(row) for row in db.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('sessions','messages','evidence') ORDER BY name")]
            original = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                        for table in ('sessions', 'messages', 'evidence')}
        self.app.store = store = ModernStore(store.path)
        snapshot, context = store.get(a), store.context(a)
        self.assertEqual([s['id'] for s in store.sessions()], [c, b, a])
        for sid in (a, b, a):
            pinned = self.app.rpc('sessions.pin', dict(sessionId=sid, pinned=True))
            self.assertEqual(pinned, {**store.get(sid)['session'], 'pinned': True})
            self.assertIs(pinned['pinned'], True)
        self.assertEqual([s['id'] for s in store.sessions()], [b, a, c])
        restored = ModernStore(store.path)
        self.assertEqual([s['id'] for s in restored.sessions()], [b, a, c])
        self.assertEqual(restored.get(a), {**snapshot, 'session': {**snapshot['session'], 'pinned': True, 'sectionId': restored.organization.sections()[0]['id'], 'sectionPosition': 1000000}})
        self.assertEqual(restored.context(a), context)
        with restored.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_pins').fetchone()[0], 2)
            self.assertEqual(schemas, [tuple(row) for row in db.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('sessions','messages','evidence') ORDER BY name")])
            self.assertEqual(original, {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
                                        for table in original})
        for _ in range(2):
            self.assertEqual(self.app.rpc('sessions.pin', dict(sessionId=a, pinned=False)), snapshot['session'])
        self.assertEqual([s['id'] for s in store.sessions()], [b, c, a])
        restored = ModernStore(store.path)
        self.assertEqual(restored.get(a), snapshot)
        self.assertEqual(restored.context(a), context)
        with restored.connect() as db:
            self.assertIsNone(db.execute('SELECT session FROM session_pins WHERE session=?', (a,)).fetchone())
        self.app.rpc('sessions.delete', dict(sessionId=b))
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_pins').fetchone()[0], 0)
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])
        self.assertEqual(self.fake.calls, [])

    def test_session_pin_rejects_non_boolean_and_missing_sessions_without_side_effects(self):
        sid = self.session()
        before, config_before = self.app.store.path.read_bytes(), self.config.read_bytes()
        with patch.object(self.app.settings, 'candidate') as candidate, patch.object(self.fake, 'capture_target') as capture, patch.object(self.fake, 'run') as execute, patch.object(budget, 'text_request') as request:
            for value in (None, 0, 1, 'true', 'false', [], {}, 1.0):
                with self.subTest(pinned=value):
                    with self.assertRaisesRegex(ValueError, '布尔值'):
                        self.app.rpc('sessions.pin', dict(sessionId=sid, pinned=value))
                    with self.assertRaisesRegex(ValueError, '布尔值'): self.app.store.pin(sid, value)
            for params in (dict(sessionId=sid), dict(sessionId='missing', pinned=True), dict(pinned=True)):
                with self.assertRaises(ValueError): self.app.rpc('sessions.pin', params)
            candidate.assert_not_called(); capture.assert_not_called(); execute.assert_not_called(); request.assert_not_called()
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(self.app.tasks, {})

    def test_session_pin_allowed_during_task_without_capture_config_or_replay(self):
        sid = self.session()
        task = self.app.submit(sid, 'pin-active')
        self.wait(lambda: self.app.tasks[task['id']]['message']['content'] == 'pin-active-stream')
        with self.app.guard:
            active = self.app.tasks[task['id']]
            message = json.dumps(active['message'], sort_keys=True)
            cursor, events = self.app.cursor, list(self.app.events)
            snapshot = self.app.store.get(sid)
            config_before = self.config.read_bytes()
            captured_config, captured_target = repr(self.fake.calls[0]['config']), self.fake.calls[0]['target']
            with patch.object(self.app.settings, 'candidate') as candidate, patch.object(self.app.settings, 'save') as save, patch.object(self.fake, 'capture_target') as capture, patch.object(self.fake, 'run') as execute, patch.object(budget, 'text_request') as request, patch.object(self.app.local, 'lease') as lease:
                for value in (True, False):
                    result = self.app.rpc('sessions.pin', dict(sessionId=sid, pinned=value))
                    self.assertEqual(result, {**snapshot['session'], 'pinned': value, 'sectionId': self.app.store.organization.sections()[0]['id'] if value else None, 'sectionPosition': 1000000 if value else None})
                candidate.assert_not_called(); save.assert_not_called(); capture.assert_not_called()
                execute.assert_not_called(); request.assert_not_called(); lease.assert_not_called()
            self.assertIs(self.app.tasks[task['id']], active)
            self.assertEqual(active['status'], 'running')
            self.assertFalse(active['cancel'].is_set())
            self.assertEqual(json.dumps(active['message'], sort_keys=True), message)
            self.assertEqual((self.app.cursor, list(self.app.events)), (cursor, events))
            self.assertEqual(self.app.store.get(sid), snapshot)
            self.assertEqual(self.config.read_bytes(), config_before)
            self.assertEqual(len(self.fake.calls), 1)
            self.assertEqual(repr(self.fake.calls[0]['config']), captured_config)
            self.assertEqual(self.fake.calls[0]['target'], captured_target)
        self.app.cancel(task['id'])

    def test_context_status_readonly_matches_compaction_summary_evidence_and_draft(self):
        sid = self.session()
        self.app.store.put_message(sid, dict(id='old', role='user', content='old history', status='complete'))
        history, _ = self.app.store.context(sid)
        self.app.store.summarize(sid, 'saved summary', history[-1]['_seq'])
        self.app.store.put_message(sid, dict(id='recent', role='user', content='recent history', status='complete'))
        self.app.store.put_message(sid, dict(id='live', role='assistant', content='excluded streaming', status='running'))
        self.app.store.save_evidence(sid, 'proof', dict(source='test', rows=['15 ms']))
        before, config_before = self.app.store.path.read_bytes(), self.config.read_bytes()
        with patch.object(self.fake, 'capture_target') as capture, patch.object(budget, 'compact') as compact, patch.object(budget, 'text_request') as request:
            status = self.app.rpc('sessions.context', dict(sessionId=sid, text='draft'))
            capture.assert_not_called(); compact.assert_not_called(); request.assert_not_called()
        history, evidence = self.app.store.context(sid)
        self.assertNotIn('excluded streaming', json.dumps(history))
        self.assertIn('saved summary', json.dumps(history))
        expected = budget.context_estimate(self.app.prepared(), history, evidence, 'draft')
        self.assertEqual(status['inputTokens'], expected['inputTokens'])
        self.assertEqual(status['availableTokens'], 32768 - expected['inputTokens'] - 1000)
        self.assertEqual(status['contextWindow'], 32768)
        self.assertEqual(status['windowSource'], 'configured')
        self.assertTrue(status['estimated'])
        self.assertEqual(self.app.store.path.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(self.app.tasks, {})
        self.assertGreater(self.app.context_status(sid, '长草稿' * 200)['inputTokens'], status['inputTokens'])

    def test_context_status_modes_unknown_gateway_and_provider_reserve(self):
        sid = self.session()
        self.app.settings.save(dict(api=dict(token_mode='auto')))
        unknown = self.app.context_status(sid)
        self.assertIsNone(unknown['percent']); self.assertIsNone(unknown['contextWindow'])
        self.assertEqual(unknown['windowSource'], 'unknown')
        self.assertTrue(unknown['reason'])
        self.app.settings.save(dict(api=dict(base_url='https://api.openai.com/v1', model='gpt-4o-mini')))
        auto = self.app.context_status(sid)
        self.assertEqual(auto['contextWindow'], 128000)
        self.assertEqual(auto['reservedTokens'], 16000)
        self.assertEqual(auto['windowSource'], 'metadata')
        self.app.settings.save(dict(api=dict(token_mode='provider')))
        provider = self.app.context_status(sid)
        self.assertEqual(provider['reservedTokens'], 12800)
        self.app.settings.save(dict(api=dict(base_url='https://gateway.example/v1', token_mode='auto')))
        self.assertIsNone(self.app.context_status(sid)['contextWindow'])
        self.app.settings.save(dict(api=dict(token_mode='provider')))
        self.assertEqual(self.app.context_status(sid)['contextWindow'], 32768)

    def test_context_status_text_attachment_and_input_limits(self):
        sid = self.session()
        base = self.app.context_status(sid)['inputTokens']
        meta = self.app.attachments.import_bytes('preview.txt', ('材料' * 500).encode())
        status = self.app.context_status(sid, attachment_ids=[meta['id']])
        self.assertGreater(status['inputTokens'], base)
        for ids in ('bad', [meta['id']] * 9, ['../config.json']):
            with self.assertRaises(ValueError): self.app.context_status(sid, attachment_ids=ids)
        with self.assertRaises(ValueError): self.app.context_status(sid, text='x' * 100001)
        large = self.app.attachments.import_bytes('large.txt', b'x' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '1 MiB'): self.app.context_status(sid, attachment_ids=[large['id']])

    def test_quick_partial_settings_preserve_credentials_and_running_task_strength(self):
        sid = self.session()
        task = self.app.submit(sid, 'quick-config')
        self.wait(lambda: bool(self.fake.calls))
        self.app.rpc('settings.save', dict(settings=dict(api=dict(model='next', thinking_level='high', force_deep_thinking=False))))
        raw = json.loads(self.config.read_text())
        self.assertEqual(raw['api']['api_key'], 'private-api-secret')
        self.assertEqual(raw['api']['base_url'], 'http://127.0.0.1:8999/v1')
        self.assertEqual(raw['qwen']['model'], 'local-user-model')
        self.assertEqual(raw['custom'], dict(preserve='yes'))
        self.assertEqual(self.fake.calls[0]['config'].api.model, 'mock')
        self.assertNotEqual(self.fake.calls[0]['config'].api.thinking_level, 'high')
        self.assertEqual(self.app.context_status(sid)['model'], 'next')
        self.app.cancel(task['id'])

    def test_live_snapshot_includes_unflushed_delta_without_cross_session_overlay(self):
        a, b = self.session(), self.session()
        task = self.app.submit(a, 'snapshot')
        self.wait(lambda: self.app.tasks[task['id']]['message']['content'] == 'snapshot-stream')
        snapshot = self.app.rpc('sessions.get', dict(sessionId=a))
        self.assertEqual(snapshot['messages'][-1]['content'], 'snapshot-stream')
        self.assertEqual(snapshot['cursor'], self.app.cursor)
        self.assertTrue(any(e['type'] == 'delta' and e['seq'] <= snapshot['cursor']
                            for e in self.app.poll(0)['events']))
        self.assertEqual(self.app.rpc('sessions.get', dict(sessionId=b))['messages'], [])
        self.app.cancel(task['id'])

    def test_background_membership_cancel_and_same_session_gate(self):
        a, b = self.session(), self.session()
        ta = self.app.submit(a, 'A')
        tb = self.app.submit(b, 'B')
        self.wait(lambda: len(self.fake.calls) == 2)
        with self.assertRaisesRegex(ValueError, '已有任务'): self.app.submit(a, 'overlap')
        with self.assertRaises(ValueError): self.app.rpc('sessions.delete', dict(sessionId=a))
        self.app.cancel(ta['id'])
        self.fake.gates['B'].set()
        self.wait(lambda: all(t['status'] not in {'running', 'cancelling'} for t in self.app.tasks.values()))
        self.assertEqual(self.app.tasks[ta['id']]['status'], 'cancelled')
        self.assertEqual(self.app.tasks[tb['id']]['status'], 'complete')
        events = self.app.poll(0)['events']
        self.assertTrue(all(e['sessionId'] == (a if e['taskId'] == ta['id'] else b) for e in events))
        self.assertIn('delivered', json.dumps(self.app.store.get(a)))
        self.assertNotIn('A-result', json.dumps(self.app.store.get(b)))

    def test_config_snapshot_and_resume_full_context_without_replay(self):
        sid = self.session()
        task = self.app.submit(sid, 'original')
        self.wait(lambda: bool(self.fake.calls))
        values = self.app.settings.get()
        values['api']['model'] = 'new-model'
        values['api']['thinking_level'] = 'high'
        self.app.settings.save(values)
        self.assertEqual(self.fake.calls[0]['config'].api.model, 'mock')
        self.fake.gates['original'].set()
        self.wait(lambda: self.app.tasks[task['id']]['status'] == 'complete')
        self.app.close()
        restored = ModernApplication(self.root / 'new', self.config, executor=self.fake)
        try:
            self.assertEqual(len(self.fake.calls), 1)
            task2 = restored.submit(sid, 'followup')
            self.wait(lambda: len(self.fake.calls) == 2)
            self.assertIn('original-result', json.dumps(self.fake.calls[1]['history']))
            self.assertIn('15 ms', json.dumps(self.fake.calls[1]['historical_evidence']))
            self.assertNotIn('历史专业证据与投递事实', json.dumps(self.fake.calls[1]['history']))
            self.fake.gates['followup'].set()
            self.wait(lambda: restored.tasks[task2['id']]['status'] == 'complete')
        finally: restored.close()

    def test_legacy_sqlite_readonly_no_copy_or_modify(self):
        path = self.root / 'legacy.sqlite3'
        old = ConversationStore(path)
        sid = old.new_session()
        old.append(sid, 'user', dict(prompt='old question'))
        old.append(sid, 'turn', dict(report='old result'))
        before = path.read_bytes()
        before_hash = hashlib.sha256(before).hexdigest()
        store = ModernStore(self.root / 'separate.sqlite3', path)
        self.assertEqual(store.get('legacy:' + sid)['messages'][1]['content'], 'old result')
        legacy_id = 'legacy:' + sid
        self.assertIs(store.get(legacy_id)['session']['pinned'], False)
        self.assertIs(store.sessions()[0]['pinned'], False)
        self.app.store = store
        with patch.object(self.app.settings, 'candidate') as candidate, patch.object(self.fake, 'capture_target') as capture, patch.object(self.fake, 'run') as execute, patch.object(budget, 'text_request') as request:
            for pinned in (True, False):
                with self.assertRaisesRegex(ValueError, '只读'): store.pin(legacy_id, pinned)
                with self.assertRaisesRegex(ValueError, '只读'):
                    self.app.rpc('sessions.pin', dict(sessionId=legacy_id, pinned=pinned))
            candidate.assert_not_called(); capture.assert_not_called(); execute.assert_not_called(); request.assert_not_called()
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_pins').fetchone()[0], 0)
        for operation in (lambda: store.rename('legacy:' + sid, 'bad'), lambda: store.view('legacy:' + sid, draft='bad')):
            with self.assertRaises(ValueError): operation()
        with store.legacy_connect() as db:
            with self.assertRaises(sqlite3.OperationalError): db.execute('DELETE FROM sessions')
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before_hash)
        with self.assertRaises(ValueError): ModernStore(path, path)

    def test_derived_token_mode_is_not_persisted_by_a_full_section_save(self):
        """2026-10-04：``token_mode`` 是给界面看的派生值，回显保存不许落盘。

        一旦落盘，所有按 ``token_mode is not None`` 分支的读者（老 Tk 窗口
        ``cloud_workflow``、本地模型的 payload）都会换一条路，而用户并没有做过
        这个选择；只有用户真的选了另一种模式才写。
        """

        raw = json.loads(self.config.read_text())
        raw['api'].pop('token_mode', None)
        self.config.write_text(json.dumps(raw), encoding='utf8')
        settings = self.app.settings.get()
        self.assertEqual(settings['api']['token_mode'], 'provider')   # 派生：limit_tokens 为假
        self.app.settings.save(settings)
        saved = json.loads(self.config.read_text())
        self.assertNotIn('token_mode', saved['api'])
        self.assertNotIn('token_mode', saved['qwen'])
        self.app.settings.save(dict(api=dict(token_mode='manual')))
        self.assertEqual(json.loads(self.config.read_text())['api']['token_mode'], 'manual')

    def test_configuration_merge_preserves_credentials_unknown_and_local(self):
        settings = self.app.settings.get()
        self.assertEqual(settings['api']['api_key'], '')
        self.assertTrue(settings['api']['has_api_key'])
        settings['api']['plan_temperature'] = .42
        self.app.settings.save(settings)
        raw = json.loads(self.config.read_text())
        self.assertEqual(raw['custom']['preserve'], 'yes')
        self.assertEqual(raw['api']['api_key'], 'private-api-secret')
        self.assertEqual(raw['qwen']['plan_temperature'], .7)
        settings['api']['clear_api_key'] = True
        self.app.settings.save(settings)
        self.assertEqual(json.loads(self.config.read_text())['api']['api_key'], '')

    def test_layout_preferences_persist_clamped_without_dropping_other_preferences(self):
        settings = self.app.settings.get()
        # The drag handles persist here because WebView2 private mode discards localStorage.
        self.assertEqual(settings['preferences']['sidebar_width'], 272)
        self.assertEqual(settings['preferences']['conversation_width'], 720)
        self.app.settings.save(dict(preferences=dict(sidebar_width=300)))
        prefs = json.loads(self.config.read_text())['modern']['preferences']
        self.assertEqual((prefs['sidebar_width'], prefs['conversation_width'], prefs['theme'], prefs['smooth_stream']), (300, 720, 'system', True))
        self.app.settings.save(dict(preferences=dict(sidebar_width=9999, conversation_width=1)))
        prefs = json.loads(self.config.read_text())['modern']['preferences']
        self.assertEqual((prefs['sidebar_width'], prefs['conversation_width']), (520, 560))
        # A garbage value falls back to the default instead of raising or persisting NaN.
        self.app.settings.save(dict(preferences=dict(sidebar_width='wide')))
        self.assertEqual(json.loads(self.config.read_text())['modern']['preferences']['sidebar_width'], 272)

    def test_reading_anchor_persists_without_disturbing_the_pixel_fallback(self):
        sid = self.session()
        self.app.rpc('sessions.view', dict(sessionId=sid, scroll=1600, anchor=dict(messageId='m-1', offset=-359)))
        session = self.app.store.get(sid)['session']
        self.assertEqual(session['anchor'], {'messageId': 'm-1', 'offset': -359.0})
        self.assertEqual(session['scroll'], 1600)
        # An additive write (a draft keystroke) must not clear the reading position.
        self.app.rpc('sessions.view', dict(sessionId=sid, draft='草稿'))
        self.assertEqual(self.app.store.get(sid)['session']['anchor'], {'messageId': 'm-1', 'offset': -359.0})
        # A windowed transcript cannot trust a pixel offset, so a bad anchor is refused outright.
        for bad in (dict(messageId='', offset=0), dict(messageId='m'), dict(messageId='m', offset=float('nan')),
                    dict(messageId='m', offset=True), dict(messageId='x' * 201, offset=0), 'not-an-object'):
            with self.assertRaises(ValueError):
                self.app.rpc('sessions.view', dict(sessionId=sid, anchor=bad))
        self.assertEqual(self.app.store.get(sid)['session']['anchor'], {'messageId': 'm-1', 'offset': -359.0})
        # A legacy read-only record never accepts one.
        with self.assertRaises(ValueError):
            self.app.rpc('sessions.view', dict(sessionId='legacy:fixture', anchor=dict(messageId='m', offset=0)))

    def test_reading_anchor_column_is_added_to_an_existing_database(self):
        old = self.root / 'pre-anchor.sqlite3'
        db = sqlite3.connect(old)
        try:
            db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,created TEXT NOT NULL,"
                             "updated TEXT NOT NULL,draft TEXT NOT NULL DEFAULT '',scroll REAL NOT NULL DEFAULT 0,"
                             "summary TEXT NOT NULL DEFAULT '',summarized INTEGER NOT NULL DEFAULT 0);")
            db.execute("INSERT INTO sessions(id,title,created,updated) VALUES ('old','旧库','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
            db.commit()
        finally:
            db.close()
        store = ModernStore(old)
        self.assertIsNone(store.get('old')['session']['anchor'])
        store.view('old', anchor=dict(messageId='m', offset=-12.5))
        self.assertEqual(store.get('old')['session']['anchor'], {'messageId': 'm', 'offset': -12.5})

    def test_sensitive_content_and_binary_excluded_from_history(self):
        sid = self.session()
        self.app.store.put_message(sid, dict(id='secret', role='user', content='private-api-secret data:audio/wav;base64,XXXXX',
                                             authorization='Bearer secret', audio_base64='XXXXX'))
        raw = json.dumps(self.app.store.get(sid))
        self.assertNotIn('private-api-secret', raw)
        self.assertNotIn('XXXXX', raw)
        self.assertNotIn('authorization', raw)

    def test_rpc_allowlist_and_origin_and_cloud_authorization(self):
        with self.assertRaises(ValueError): self.app.rpc('__dict__', {})
        with self.assertRaises(PermissionError): assert_endpoint('https://api.openai.com/v1')
        with self.assertRaises(ValueError): assert_endpoint('file:///C:/secret')
        with self.assertRaises(ValueError): assert_endpoint('http://user:pass@localhost/v1')
        api = HostAPI(self.app)
        self.assertEqual([k for k in dir(api) if not k.startswith('_')], ['rpc'])
        api._origin = 'http://127.0.0.1:1234'
        self.app.window = type('Window', (), dict(get_current_url=lambda s: 'https://untrusted.example'))()
        with self.assertRaises(ValueError): api.rpc('bootstrap', {})
        values = self.app.settings.get()
        values['api']['base_url'] = 'https://api.openai.com/v1'
        with patch.object(budget, 'text_request') as request:
            with self.assertRaises(PermissionError): self.app.test_connection('text', values)
            request.assert_not_called()

    def test_attachment_path_preview_and_no_binary_message(self):
        meta = self.app.attachments.import_bytes('../example.txt', '测试材料'.encode())
        self.assertEqual(meta['name'], 'example.txt')
        self.assertEqual(self.app.attachments.preview(meta['id'])['text'], '测试材料')
        with self.assertRaises(ValueError): self.app.attachments.resolve('../config.json')
        with self.assertRaises(ValueError): self.app.attachments.import_bytes('attack.html', b'<script>')

    def test_interrupted_task_never_resumed(self):
        sid = self.session()
        self.app.store.put_message(sid, dict(id='partial', role='assistant', status='running', content='partial text', activities=[dict(execution='execution_unknown')]))
        restored = ModernStore(self.app.store.path)
        message = restored.get(sid)['messages'][0]
        self.assertEqual(message['status'], 'interrupted')
        self.assertIn('execution_unknown', json.dumps(message))
        self.assertEqual(self.fake.calls, [])

    def test_local_lease_deferred_stop_does_not_unload_in_use_or_unowned(self):
        resource = LocalResources()
        calls = []
        resource.manager = type('Manager', (), dict(stop=lambda self: calls.append('stop')))()
        config = AppConfig()
        with resource.lease(config, threading.Event(), lambda *args: None):
            resource.request_stop()
            self.assertEqual(calls, [])
        self.assertEqual(calls, ['stop'])
        resource.request_stop()
        self.assertEqual(calls, ['stop'])

    def test_configured_local_model_starts_on_demand_despite_legacy_auto_start(self):
        config = AppConfig()
        config.server.llama_server = 'configured-llama-server.exe'
        config.server.model_path = 'configured-model.gguf'
        resource = LocalResources()
        with patch('praat_ai.server.QwenServerManager') as factory, patch('praat_ai.vram.detect_gpu', return_value=None):
            with resource.lease(config, threading.Event(), lambda *args: None):
                factory.assert_called_once()
                factory.return_value.ensure_started.assert_called_once()
            self.assertTrue(factory.call_args.args[0].server.auto_start)
            self.assertFalse(config.server.auto_start)
            with resource.lease(config, threading.Event(), lambda *args: None):
                self.assertEqual(factory.call_count, 1)
                self.assertEqual(factory.return_value.ensure_started.call_count, 2)
            resource.request_stop()

    def test_unmanaged_endpoint_does_not_launch_configured_local_files(self):
        config = AppConfig()
        config.server.llama_server = 'configured-llama-server.exe'
        config.server.model_path = 'configured-model.gguf'
        for address in ('https://example.invalid/v1', 'http://127.0.0.1:8999/v1'):
            config.qwen.base_url = address
            with patch('praat_ai.server.QwenServerManager') as factory:
                with LocalResources().lease(config, threading.Event(), lambda *args: None):
                    factory.assert_not_called()

    def test_local_connection_test_waits_for_model_lease(self):
        from contextlib import contextmanager
        order = []
        @contextmanager
        def lease(*args):
            order.append('ready')
            yield
            order.append('released')
        self.app.settings.save({'api': {'enabled': False}})
        with patch.object(self.app.local, 'lease', side_effect=lease), patch.object(
                budget, 'text_request', side_effect=lambda *args: order.append('request') or 'OK'):
            result = self.app.test_connection('text', {})
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(order, ['ready', 'request', 'released'])

    def test_local_load_failure_prevents_connection_request(self):
        self.app.settings.save({'api': {'enabled': False}})
        with patch.object(self.app.local, 'lease', side_effect=RuntimeError('模型加载失败')), patch.object(budget, 'text_request') as request:
            result = self.app.test_connection('text', {})
        self.assertEqual(result['status'], 'failed')
        self.assertIn('模型加载失败', result['reason'])
        request.assert_not_called()

    def test_new_token_modes_request_fields_and_sdk_same_policy(self):
        from praat_ai.cloud_agent import CloudSession
        from praat_ai.escape_policy import AnalysisState, Budget
        for mode in ('auto', 'manual', 'provider'):
            cfg = self.app.settings.candidate()
            cfg.api.token_mode = mode
            cfg.api.top_p, cfg.api.presence_penalty = .9, .4
            cfg.api.base_url = 'https://api.openai.com/v1'
            cfg.api.model = 'gpt-4o-mini'
            cfg = budget.prepare(cfg, explicit_window=True)
            expected = budget.model_settings(cfg.api)
            session = CloudSession(cfg, AnalysisState('hello', '', budget=Budget(cfg.api.max_context_tokens, cfg.api.plan_max_tokens)),
                                   None, threading.Event(), None, object(), [], None, None, None)
            session.set_body_tokens(2048)
            session.fit_output(50)
            self.assertEqual(session.settings.get('max_tokens'), expected.get('max_tokens'))
            self.assertEqual(session.settings['presence_penalty'], .4)
            if mode == 'provider': self.assertNotIn('max_tokens', session.settings)
            self.assertEqual(session.state.budget.context_tokens, cfg.api.max_context_tokens)
        cfg = self.app.settings.candidate()
        cfg.api.token_mode = 'auto'
        with self.assertRaises(ValueError): budget.prepare(cfg)

    def test_qwen_provider_mode_omits_cap_and_keeps_retry_field(self):
        cfg = QwenConfig(provider='api', token_mode='provider', limit_tokens=False)
        captured = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'
        def opened(request, **kwargs):
            captured.append(json.loads(request.data))
            return Response()
        with patch('praat_ai.qwen._open_no_redirect', opened):
            qwen.QwenClient(cfg).chat([dict(role='user', content='hello')])
            self.assertNotIn('max_tokens', captured[-1])
            cfg.token_mode, cfg.plan_max_tokens = 'manual', 512
            qwen.QwenClient(cfg)._post(dict(messages=[], max_completion_tokens=512))
            self.assertEqual(captured[-1]['max_completion_tokens'], 512)
            self.assertNotIn('max_tokens', captured[-1])

    def test_real_compaction_one_pass_all_modes_preserves_original_and_evidence(self):
        for mode in ('auto', 'manual', 'provider'):
            sid = self.session()
            for i in range(12):
                self.app.store.put_message(sid, dict(id=f'{sid}-{i}', role='user' if i % 2 == 0 else 'assistant', content='a' * 1500, status='complete'))
            self.app.store.save_evidence(sid, 'old-task', dict(evidence=[dict(rows=['15 ms'], source='Sound 1 range 0-1')], attempts=[dict(execution='delivered')]))
            history, evidence = self.app.store.context(sid)
            cfg = AppConfig()
            cfg.qwen.token_mode, cfg.qwen.max_context_tokens, cfg.qwen.plan_max_tokens = mode, 5120, 128
            events = []
            requests = []
            def summarize(config, messages, **kwargs):
                requests.append(messages)
                return '真实摘要替身：原目标和范围保留'
            with patch.object(qwen, 'estimate_tokens', lambda t: len(t) // 4), patch.object(qwen, 'planner_instructions', lambda c: ''), patch('praat_ai.tools.tool_schemas', return_value=[]):
                result = budget.compact(self.app.store, sid, cfg, history, evidence, 'next', 'bound', cancel=threading.Event(), emit=lambda *a: events.append(a), requester=summarize)
            self.assertEqual(len(requests), 1)
            self.assertEqual(len(self.app.store.get(sid)['messages']), 12)
            self.assertEqual(len(evidence), 1)
            self.assertEqual(len(result), 7)
            self.assertEqual(events[0][1]['type'], 'compression')

    def test_modern_unset_sampling_is_not_replaced_with_local_defaults(self):
        settings = self.app.settings.get()
        settings['api'].update(plan_temperature=None, top_p=None, presence_penalty=None)
        self.app.settings.save(settings)
        config = self.app.prepared()
        self.assertIsNone(config.qwen.plan_temperature)
        fields = budget.model_settings(config.api)
        self.assertFalse({'temperature', 'top_p', 'presence_penalty'} & fields.keys())

    def test_candidate_key_redacted_from_test_response(self):
        values = self.app.settings.get()
        values['api']['api_key'] = 'fresh-candidate-secret'
        with patch.object(budget, 'text_request', return_value='echo fresh-candidate-secret'):
            result = self.app.test_connection('text', values)
        self.assertNotIn('fresh-candidate-secret', json.dumps(result))
        self.assertIn('隐藏', result['details'])

    def test_cancel_during_evidence_save_wins_final_commit(self):
        sid = self.session()
        entered, release = threading.Event(), threading.Event()
        original = self.app.store.save_evidence
        def save(*args):
            entered.set()
            if not release.wait(3): raise RuntimeError('test barrier timeout')
            return original(*args)
        with patch.object(self.app.store, 'save_evidence', side_effect=save):
            task = self.app.submit(sid, 'late-cancel')
            self.wait(lambda: 'late-cancel' in self.fake.gates)
            self.fake.gates['late-cancel'].set()
            self.assertTrue(entered.wait(2))
            try:
                self.app.cancel(task['id'])
            finally:
                release.set()
            self.wait(lambda: self.app.tasks[task['id']]['status'] == 'cancelled')
        self.assertIn('delivered', json.dumps(self.app.store.context(sid)[1]))

    def test_modern_json_planner_preserves_earliest_history(self):
        client = qwen.QwenClient(QwenConfig(token_mode='manual', limit_tokens=True))
        history = [dict(role='user', content=f'constraint-{i}') for i in range(10)]
        with patch.dict(os.environ, {'PRAAT_AI_PLANNER':'json'}), patch.object(client, 'chat', return_value='{"reply":"OK"}') as request:
            client.plan_praat_command('next', '', history, tool_catalog='', result_path='result', state_path='state')
        messages = request.call_args.args[0]
        self.assertIn('constraint-0', json.dumps(messages))
        self.assertEqual(len(messages), 12)

    def test_modern_report_preserves_full_evidence_and_resumed_dialogue(self):
        from praat_ai.cloud_agent import report_context, compact_report_context
        from praat_ai.escape_policy import AnalysisState, Evidence
        state = AnalysisState('goal', 'original-source', mode='L2')
        state.evidence = [Evidence('duration', {}, ['x' * 3000] * 45, 'source')]
        state.dialogue_context = [dict(role='user', content='historic evidence 15 ms')]
        context = report_context(state, preserve=True)
        result = json.loads(compact_report_context(context, 100, preserve=True))
        self.assertEqual(result['evidence'][0]['rows'], state.evidence[0].rows)
        self.assertEqual(result['visible_dialogue'], state.dialogue_context)
        self.assertNotIn('excerpt_note', result)

    def test_text_attachment_budget_included_before_execution(self):
        sid = self.session()
        attachment = self.app.attachments.import_bytes('material.txt', b'large user material')
        seen = []
        def compact(store, session, config, history, evidence, text, target, **kwargs):
            seen.append(text)
            raise ValueError('material cannot fit')
        with patch.object(budget, 'compact', side_effect=compact):
            task = self.app.submit(sid, 'question', [attachment['id']])
            self.wait(lambda: self.app.tasks[task['id']]['status'] == 'failed')
        self.assertIn('large user material', seen[0])
        self.assertEqual(self.fake.calls, [])


if __name__ == '__main__': unittest.main()
