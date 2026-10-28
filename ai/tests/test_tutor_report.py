import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from praat_ai.models import AnalysisResult, FeatureTrack, PhoneFeatureTracks, PronunciationError
from praat_ai.modern_app import ModernApplication
from praat_ai.tutor import TutorOutputs, run_from_praat
from praat_ai.tutor_report import import_report, number, queue_report, render_report


class TutorReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'api': {'enabled': False}, 'server': {'auto_start': False}}))
        self.app = ModernApplication(self.root / 'modern', self.config, executor=Mock())
        self.addCleanup(self.app.close)
        phone = PhoneFeatureTracks(1, 'a', 0, .2, {'pitch': FeatureTrack('pitch', [.01], [220], 'Hz')})
        self.result = AnalysisResult('en', '2_Sound_standard.wav', '4_Sound_learner.wav', '', '', .2,
            [PronunciationError(1, 'a', 0, .2, 'pitch', 1, 1, '偏低', '基频偏低', 220, 200, 'Hz')],
            [phone], [phone], alignment_source='mfa', alignment_confidence=.85,
            check_statuses={'phone_acoustics': 'partial'},
            phone_assessments=[{'phone_index': 1, 'ipa': 'a', 'status': 'partial',
                'checks': [{'feature': 'f1', 'status': 'insufficient_evidence', 'reason': '没有可用共振峰'}]}])
        reports = self.root / 'reports' / 'one'
        reports.mkdir(parents=True)
        self.json = reports / 'ai_report.json'
        self.json.write_text(json.dumps(self.result.to_dict(), ensure_ascii=False), encoding='utf8')
        self.grid = reports / 'ai_errors.TextGrid'
        self.grid.write_text('File type = "ooTextFile"\n', encoding='utf8')
        self.png = reports / 'ai_overlay.png'
        from PIL import Image
        Image.new('RGB', (50, 30), 'white').save(self.png)
        self.outputs = TutorOutputs(self.json, self.grid, self.png, self.result)

    def test_visible_report_preserves_values_missing_reasons_and_both_analysis_modes(self):
        text = render_report(self.result.to_dict())
        self.assertIn('1 项声学差异', text)
        self.assertIn('| 220 Hz | 200 Hz |', text)
        self.assertIn('0–0.2', text)
        self.assertIn('没有可用共振峰', text)
        self.assertIn('本次未生成 AI 文字解释', text)
        payload = self.result.to_dict()
        payload.update(profile_snapshot={'name': 'test'}, observations=payload['errors'], explanation='已核对的解释')
        text = render_report(payload)
        self.assertIn('1 项音位观察', text)
        self.assertIn('已核对的解释', text)
        for value, expected in ((0, '0'), (-12.5, '-12.5'), (None, '未测得'), (float('nan'), '未测得')):
            self.assertEqual(number(value), expected)

    def test_poll_imports_once_preserves_running_chats_and_keeps_files_after_source_removal(self):
        existing = self.app.store.new_session('working')
        self.app.store.put_message(existing['id'], {'id': 'running', 'role': 'assistant', 'content': 'active', 'status': 'running'})
        key = queue_report(self.outputs, runtime=self.root)
        first = self.app.poll(0)
        self.assertEqual(first['navigation']['sessionId'], key)
        report = self.app.get_session(key)
        self.assertEqual(report['session']['anchor'], {'messageId': key, 'offset': 0})
        self.assertEqual(len(report['messages']), 1)
        message = report['messages'][0]
        self.assertEqual(message['report']['type'], 'pronunciation')
        self.assertEqual(len(message['attachments']), 3)
        history, evidence = self.app.store.context(key)
        self.assertIn('AI 纠音报告', history[0]['content'])
        self.assertTrue(evidence[0]['evidence'])
        self.assertEqual(self.app.store.get(existing['id'])['messages'][0]['status'], 'running')
        self.assertNotIn('navigation', self.app.poll(0))
        queue_report(self.outputs, runtime=self.root)
        self.assertEqual(self.app.poll(0)['navigation']['sessionId'], key)
        self.assertEqual(len(self.app.store.sessions()), 2)
        self.json.unlink(); self.grid.unlink(); self.png.unlink()
        for attachment in message['attachments']:
            preview = self.app.attachments.preview(attachment['id'])
            self.assertTrue(preview.get('text') or preview.get('dataUrl'))

    def test_bad_report_is_visible_once_without_blocking_other_events(self):
        queue_report(self.outputs, runtime=self.root)
        self.json.write_text('not json')
        result = self.app.poll(0)
        self.assertIn('报告显示失败', result['navigation']['error'])
        self.assertNotIn('navigation', self.app.poll(0))
        self.assertEqual(len(list((self.root/'tutor-reports').glob('*.failed'))), 1)

    def test_report_rejects_an_attachment_outside_its_report_directory(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        with self.assertRaisesRegex(ValueError, '附件路径'):
            import_report(self.app, {'schema':'tutor-chat-report-v1', 'source':str(self.json), 'files':[str(outside)]})
        self.assertEqual(self.app.store.sessions(), [])

    def test_interactive_entry_opens_chat_without_printing_a_path_only_report(self):
        with patch('praat_ai.tutor.run_with_stop', return_value=(self.outputs, False)), \
             patch('praat_ai.tutor_report.queue_report') as queued, \
             patch('start_ai_chat.main', return_value=0) as opened:
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_from_praat(request_path='fixture.json'), 0)
        queued.assert_called_once_with(self.outputs)
        opened.assert_called_once_with()
        self.assertEqual(output.getvalue(), '')

    def test_cli_keeps_file_output_and_does_not_open_chat(self):
        with patch('praat_ai.tutor.run_with_stop', return_value=(self.outputs, False)), \
             patch('start_ai_chat.main') as opened:
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_from_praat(request_path='fixture.json', interactive=False), 0)
        self.assertIn('JSON', output.getvalue())
        opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
