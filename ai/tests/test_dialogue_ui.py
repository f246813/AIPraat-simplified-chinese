"""Streaming transcript uses real Tk text indices and preserves other messages."""
import tkinter as tk
import unittest
from praat_ai import chat, ui_windows


class StreamUiTests(unittest.TestCase):
    def setUp(self):
        self.root=ui_windows.create_root(); self.root.withdraw()
        self.addCleanup(self.close)
        self.window=chat.ChatWindow.__new__(chat.ChatWindow)
        self.window.transcript=tk.Text(self.root)
        self.window.transcript.insert('end','existing user message\n')
        self.finals=[]
        def append(role,text):
            self.finals.append((role,text))
            self.window.transcript.configure(state='normal')
            self.window.transcript.insert('end',text+'\n')
            self.window.transcript.configure(state='disabled')
        self.window.append=append

    def close(self):
        self.root.update_idletasks()
        for callback in self.root.tk.splitlist(self.root.tk.call('after','info')): self.root.after_cancel(callback)
        self.root.destroy()

    def test_partial_text_is_visible_and_replaced_by_one_final_message(self):
        begin=getattr(self.window,'_begin_stream_reply',None)
        self.assertTrue(callable(begin), 'Missing UI streaming renderer')
        begin(); self.window._append_stream_reply('你好😀\n')
        self.assertIn('你好😀',self.window.transcript.get('1.0','end'))
        self.window.transcript.configure(state='normal')
        self.window.transcript.insert('end','separate stop hint\n')
        self.window.transcript.configure(state='disabled')
        self.window._append_stream_reply('続き')
        self.window._finish_stream_reply('完整回答')
        text=self.window.transcript.get('1.0','end')
        self.assertIn('existing user message',text)
        self.assertIn('separate stop hint',text)
        self.assertNotIn('你好',text); self.assertNotIn('続き',text)
        self.assertEqual(self.finals,[('assistant','完整回答')])
        self.assertEqual(self.window.transcript.cget('state'),'disabled')

    def test_late_delta_after_final_is_ignored(self):
        begin=getattr(self.window,'_begin_stream_reply',None)
        self.assertTrue(callable(begin), 'Missing UI streaming renderer')
        begin(); self.window._finish_stream_reply('已取消')
        self.window._append_stream_reply('late output')
        self.assertNotIn('late output',self.window.transcript.get('1.0','end'))

    def test_real_process_hints_survive_stream_finalization_and_collapse(self):
        window=self.window; window._process_blocks={}; window._active_process=None
        window._begin_process(); block=window._active_process
        window._begin_stream_reply()
        window.append_hint('first process hint')
        window._append_stream_reply('partial answer')
        window.append_hint('second process hint')
        window._finish_stream_reply('final answer')
        window._finish_process(block.started_at+1)
        text=window.transcript.get('1.0','end')
        self.assertIn('first process hint',text)
        self.assertIn('second process hint',text)
        self.assertNotIn('partial answer',text)
        self.assertIn('final answer',text)
        self.assertTrue(window.transcript.tk.getboolean(window.transcript.tag_cget(block.body_tag,'elide')))


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__=='__main__': unittest.main()
