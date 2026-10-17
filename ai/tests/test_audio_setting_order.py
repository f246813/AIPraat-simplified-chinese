"""音频设置的先后顺序：同一刻的两次写入也必须分得清。

Windows 的 ``datetime.now`` 粒度约 15 ms：运行时纠正和用户的手动选择经常落在**同一个
时间戳**上，而「谁更晚」是覆盖规则的唯一依据——并列会让用户的选择随机被覆盖（实测两个
用例约一半运行会失败）。这里用写序号把并列拆开，不依赖运气。

这两个方向的期望是相反的，所以必须用序号而不是「并列时偏向某一侧」来定序：
手动改在纠正之后 → 改生效；手动改在纠正之前 → 纠正生效。
"""

import json
import tempfile
import unittest
from pathlib import Path

from praat_ai import api_diagnostics, api_settings
from praat_ai.config import load_config

BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
MODEL = 'qwen3.7-flash'
KEY = 'fixture-secret'
#: 两个方向的写入都用同一个时间戳，强制进入「并列」分支。
TIED_AT = '2026-10-17T00:00:00+00:00'
REASON = 'status_code: 400; model does not support audio input'


class ObservationOrderTests(unittest.TestCase):
    def test_sequence_breaks_a_tied_timestamp(self) -> None:
        older = {'tested_at': TIED_AT, 'sequence': 1}
        newer = {'tested_at': TIED_AT, 'sequence': 2}

        self.assertTrue(api_diagnostics.observed_later(newer, older))
        self.assertFalse(api_diagnostics.observed_later(older, newer))
        # 两边都没有序号：不声称更晚（保守：保留已存的纠正）。
        self.assertFalse(api_diagnostics.observed_later({}, {}))

    def test_manual_edit_counts_as_after_only_when_it_saw_that_write(self) -> None:
        previous = {'tested_at': TIED_AT, 'sequence': 1}

        self.assertTrue(api_diagnostics.edited_after(TIED_AT, 1, previous))
        self.assertFalse(api_diagnostics.edited_after(TIED_AT, 0, previous))
        self.assertTrue(api_diagnostics.edited_after('2026-10-17T00:00:01+00:00', 0, previous))
        self.assertFalse(api_diagnostics.edited_after('2026-10-16T23:59:59+00:00', 9, previous))

    def test_next_sequence_always_increases(self) -> None:
        payload = {'audio_setting_sequence': 3, 'audio_test_result': {'sequence': 7}}

        self.assertEqual(api_diagnostics.observation_sequence(payload), 7)
        self.assertEqual(api_diagnostics.next_sequence(payload), 8)
        self.assertEqual(api_diagnostics.next_sequence({}), 1)


class SaveOrderTests(unittest.TestCase):
    """两个方向在时间戳并列时都必须稳定。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'ai_config.json'
        record = api_diagnostics.test_record(BASE_URL, MODEL, KEY,
                                            {'status': 'unsupported', 'reason': REASON}, sequence=1)
        record['tested_at'] = TIED_AT
        self.path.write_text(json.dumps({
            'api': {
                'enabled': True, 'base_url': BASE_URL, 'model': MODEL, 'api_key': KEY,
                'audio_input_enabled': False, 'audio_input_source': 'corrected',
                'audio_input_reason': REASON, 'audio_input_path': '',
                'audio_verified_at': '', 'audio_corrected_at': TIED_AT,
                'audio_setting_sequence': 1, 'audio_test_result': record,
            }
        }, ensure_ascii=False), encoding='utf-8')

    def values(self) -> dict:
        return {
            'enabled': True, 'base_url': BASE_URL, 'model': MODEL, 'api_key': KEY,
            'audio_input_enabled': True, 'audio_input_source': 'manual',
            'audio_input_reason': '用户声明；尚未实测', 'audio_input_path': '',
            'audio_verified_at': '', 'audio_test_result': {},
        }

    def test_manual_edit_after_a_tied_correction_wins(self) -> None:
        api_settings.save_settings(self.values(), self.path,
                                   audio_setting_edited_at=TIED_AT,
                                   audio_setting_edited_sequence=1)

        saved = load_config(self.path).api
        self.assertTrue(saved.audio_input_enabled)
        self.assertEqual(saved.audio_input_source, 'manual')

    def test_manual_edit_before_a_tied_correction_loses(self) -> None:
        api_settings.save_settings(self.values(), self.path,
                                   audio_setting_edited_at=TIED_AT,
                                   audio_setting_edited_sequence=0)

        saved = load_config(self.path).api
        self.assertFalse(saved.audio_input_enabled)
        self.assertEqual(saved.audio_input_source, 'corrected')

    def test_correction_wins_over_an_older_queued_success(self) -> None:
        """验证在纠正之前看到的状态：时间并列也不能反过来覆盖纠正。"""
        values = self.values()
        values['audio_input_enabled'] = True
        values['audio_input_source'] = 'manual'
        values['audio_test_result'] = dict(
            api_diagnostics.test_record(BASE_URL, MODEL, KEY,
                                        {'status': 'verified', 'reason': '基础音频特征校验通过'},
                                        # 这次验证是在纠正之前开始的：它只看到第 0 次写入。
                                        sequence=0),
            tested_at=TIED_AT)

        api_settings.save_settings(values, self.path,
                                   audio_setting_edited_at='', audio_setting_edited_sequence=0)

        saved = load_config(self.path).api
        self.assertFalse(saved.audio_input_enabled)
        self.assertEqual(saved.audio_test_result['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
