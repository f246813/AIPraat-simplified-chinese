"""Integration uses synthetic WAVs and simulated MFA, never downloads models."""
import json
import os
import sys
import tempfile
import types
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from praat_ai.bridge import SelectedObject
from praat_ai.mfa_resources import MfaResourceService
from praat_ai.ui import TutorFormApplication
from praat_ai.tutor import run_tutor


class MfaPipelineTests(unittest.TestCase):
    def test_registered_preset_g2p_submit_both_alignments_and_reports(self):
        with tempfile.TemporaryDirectory(prefix='MFA 链路 ') as directory:
            root = Path(directory)
            phones = ['HH', 'AH0', 'L', 'OW1']
            for kind in ('g2p', 'acoustic'):
                with zipfile.ZipFile(root / (kind + '.zip'), 'w') as archive:
                    archive.writestr('meta.json', json.dumps(dict(model_type=kind, language='en', phones=phones)))
            dictionary = root / 'english.dict'
            dictionary.write_text('hello HH AH0 L OW1\n', encoding='utf-8')
            config = root / 'config.json'
            config.write_text(json.dumps(dict(alignment=dict(backend='wav2vec2', mfa=dict(acoustic_model='old-global')))), encoding='utf-8')
            resources = MfaResourceService(config)
            for kind, path in (('g2p', root / 'g2p.zip'), ('dictionary', dictionary), ('acoustic', root / 'acoustic.zip')):
                resources.add(kind, [str(path)])
            data = resources.list()
            preset = next(p for p in data['presets'] if p['id'] == 'en-US')
            preset.update({kind: data['resources'][kind][0]['id'] for kind in ('g2p', 'dictionary', 'acoustic')})
            preset.update(phone_set='arpabet', confirmed=True)
            resources.save(preset, dict(executable=sys.executable, conda_executable='', conda_environment=''))
            sounds = []
            for index, name in ((1, 'standard'), (2, 'learner')):
                path = root / (name + '.wav')
                samples = (10000 * np.sin(np.arange(16000) * 2 * np.pi * (150 + index * 10) / 16000)).astype('<i2')
                with wave.open(str(path), 'wb') as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(samples.tobytes())
                sounds.append(SelectedObject(index, name, 'Sound', path))
            app = TutorFormApplication(sounds, config)
            app.window = Mock()
            def fake_g2p(command, *args):
                if command[-1] == '--version':
                    return '3.3.0'
                if command[-1] == '--help':
                    return 'Usage: mfa g2p INPUT_PATH G2P_MODEL_PATH OUTPUT_PATH --num_pronunciations'
                self.assertEqual(Path(command[2]).read_text(encoding='utf-8'), 'hello\n')
                Path(command[4]).write_text('hello HH AH0 L OW1\n', encoding='utf-8')
                return ''
            with patch('praat_ai.mfa_g2p.run_command', side_effect=fake_g2p):
                generated = app.rpc('tutor.generate_phonemes', dict(preset_id='en-US', transcript='hello'))
            candidate = generated['words'][0]['candidates'][0]
            app.rpc('tutor.submit', dict(reference='1', learner='2', language='en', preset_id='en-US',
                                       transcript='hello', phonemes=' '.join(candidate['ipa']), generation_id=generated['id'],
                                       choices={'hello': 0}, explain=False, vision=False, threshold=1.25))
            calls, selected_paths = [], []
            def fake_align(command, cancel=None, timeout=None, cwd=None):
                self.assertEqual(command[1], 'align')
                self.assertEqual(command[4], str(root / 'acoustic.zip'))
                selected = Path(command[3])
                self.assertEqual(selected.read_text(encoding='utf-8'), 'hello\tHH AH0 L OW1\n')
                selected_paths.append(selected)
                self.assertEqual((Path(command[2]) / 'learner.txt').read_text(encoding='utf-8'), 'hello\n')
                output = Path(command[5]) / 'learner.TextGrid'
                output.write_text('name = "phones"\n' + ''.join(
                    f'xmin = {index/4}\nxmax = {(index+1)/4}\ntext = "{phone}"\n'
                    for index, phone in enumerate(phones)), encoding='utf-8')
                calls.append(command)
                return 'ok\n'
            helper = types.SimpleNamespace(get_selected=lambda: [s.to_dict() for s in sounds],
                                           get_output_dir=lambda: str(root / 'imports'))
            with patch.dict(sys.modules, {'praat': helper}), patch.dict(os.environ, {'PRAAT_AI_REPORT_DIR': str(root / 'reports')}), \
                 patch('praat_ai.forced_alignment.run_mfa_command', side_effect=fake_align), \
                 patch('praat_ai.tutor.detect_gpu', return_value=None):
                outputs = run_tutor(config_path=config, request=app.result.request)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(not path.exists() for path in selected_paths))
            report = json.loads(outputs.json_path.read_text(encoding='utf-8'))
            self.assertEqual(report['generation_source'], generated['source'])
            self.assertEqual(report['selected_readings'][0]['raw'], phones)
            self.assertEqual([p['ipa'] for p in report['learner_phones']], candidate['ipa'])
            self.assertEqual([p['ipa'] for p in report['reference_phones']], candidate['ipa'])
            self.assertEqual(report['alignment_source'], 'mfa')
            grid = outputs.textgrid_path.read_text(encoding='utf-8')
            for phone in report['learner_phones']:
                self.assertIn(f'xmin = {phone["start"]:.6f}', grid)
                self.assertIn(f'xmax = {phone["end"]:.6f}', grid)
            self.assertEqual(json.loads(config.read_text(encoding='utf-8'))['alignment']['mfa']['acoustic_model'], 'old-global')
            self.assertEqual(dictionary.read_text(encoding='utf-8'), 'hello HH AH0 L OW1\n')


if __name__ == '__main__':
    unittest.main()
