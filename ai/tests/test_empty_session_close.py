"""Window lifecycle regressions using isolated real conversation databases."""
import json
import tempfile
import unittest
from pathlib import Path

from praat_ai.conversation_store import ConversationStore
from praat_ai.modern_app import ModernApplication


class NoExecution:
    def capture_target(self, text):
        raise AssertionError('Opening an empty window must not capture Praat')

    def run(self, **kwargs):
        raise AssertionError('Opening an empty window must not run a model')


class EmptySessionCloseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'api': {'enabled': False},
                                           'server': {'auto_start': False}}), encoding='utf8')
        self.legacy = ConversationStore(self.root / 'legacy.sqlite3')
        self.legacy_id = self.legacy.new_session()
        self.app = self.open_window()

    def open_window(self):
        app = ModernApplication(self.root / 'modern', self.config, self.legacy.path,
                                executor=NoExecution())
        self.addCleanup(app.close)
        return app

    def create(self, **params):
        return self.app.rpc('sessions.create', params)['id']

    def test_repeated_open_close_removes_every_blank_session(self):
        # A blank opened before this window existed is not in its created set, but
        # the close-time scan still finds and removes it.
        leftover = self.app.store.new_session()['id']
        for _ in range(3):
            self.app.rpc('bootstrap', {})
            sid = self.create()
            self.assertEqual(self.app.get_session(sid)['messages'], [])
            # Default reading-position writes also happen without a conversation.
            self.app.rpc('sessions.view', dict(sessionId=sid, draft='', scroll=0))
            self.app.close()
            self.app.close()  # closed event and host finally both call close
            self.app = self.open_window()
            self.assertEqual({s['id'] for s in self.app.bootstrap()['sessions']}, {'legacy:' + self.legacy_id})
        self.assertNotIn(leftover, {s['id'] for s in self.app.store.sessions()})

    def test_close_preserves_messages_and_nonempty_drafts(self):
        chatted, drafted, cleared = self.create(), self.create(), self.create()
        self.app.store.put_message(chatted, dict(id='user', role='user', content='你好'))
        self.app.rpc('sessions.view', dict(sessionId=drafted, draft='未发送的草稿'))
        self.app.rpc('sessions.view', dict(sessionId=cleared, draft='曾输入'))
        self.app.rpc('sessions.view', dict(sessionId=cleared, draft=''))
        self.app.close()
        self.app = self.open_window()
        self.assertEqual({s['id'] for s in self.app.store.sessions()},
                         {chatted, drafted, 'legacy:' + self.legacy_id})
        self.assertEqual(self.app.store.get(chatted)['messages'][0]['content'], '你好')
        self.assertEqual(self.app.store.get(drafted)['session']['draft'], '未发送的草稿')

    def test_close_preserves_intentionally_organized_empty_sessions(self):
        named = self.create(title='我的计划')
        renamed = self.create()
        self.app.rpc('sessions.rename', dict(sessionId=renamed, title='稍后继续'))
        pinned = self.create()
        self.app.rpc('sessions.pin', dict(sessionId=pinned, pinned=True))
        section = self.app.rpc('sections.create', dict(name='研究'))['id']
        member = self.create(sectionId=section)
        fork = self.app.rpc('sessions.fork', dict(sessionId=named))['id']
        self.app.close()
        self.app = self.open_window()
        self.assertEqual({s['id'] for s in self.app.store.sessions()},
                         {named, renamed, pinned, member, fork, 'legacy:' + self.legacy_id})

    def test_prune_script_removes_blanks_and_keeps_used_history(self):
        from praat_ai.prune_blank_sessions import prune
        blank = self.app.store.new_session()['id']
        named = self.create(title='我的计划')
        drafted = self.create()
        self.app.rpc('sessions.view', dict(sessionId=drafted, draft='未发送的草稿'))
        # Run the standalone entry while the window is still open: closing already scans.
        removed = prune(self.app.store.path, self.legacy.path)
        self.assertIn(blank, removed)
        self.assertNotIn(named, removed)
        self.assertNotIn(drafted, removed)
        self.assertEqual({s['id'] for s in self.app.store.sessions()},
                         {named, drafted, 'legacy:' + self.legacy_id})

    def test_save_empty_sessions_preference_keeps_blanks_on_close(self):
        self.app.rpc('settings.save', dict(settings={'preferences': {'save_empty_sessions': True, 'empty_session_policy': 'never_persist'}}))
        sid = self.create()
        self.app.close()
        self.app = self.open_window()
        self.assertIn(sid, {s['id'] for s in self.app.store.sessions()})

    def test_manually_deleted_empty_session_does_not_break_close(self):
        sid = self.create()
        self.app.rpc('sessions.delete', dict(sessionId=sid))
        self.app.close()
        self.assertEqual({s['id'] for s in self.app.store.sessions()}, {'legacy:' + self.legacy_id})


if __name__ == '__main__':
    unittest.main()
