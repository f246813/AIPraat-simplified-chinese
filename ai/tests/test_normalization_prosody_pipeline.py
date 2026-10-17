import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
from praat_ai.bridge import SelectedObject
from praat_ai.models import AnalysisRequest, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.report import write_json, write_textgrid, write_overlay_png
from praat_ai.normalization import VERSION
from praat_ai.normalization_prosody import fit_pitch_baseline, PITCH_DEFAULTS
from praat_ai.ui import TutorFormApplication
from test_normalization_prosody import track


def wav(path, frequency, duration=1, gain=1):
    sr=48000; t=np.arange(int(sr*duration))/sr
    samples=np.sin(t*2*np.pi*frequency)*.1*gain
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr)
        f.writeframes((samples*32767).astype('<i2').tobytes())


class ProsodyPipelineTests(unittest.TestCase):
    def test_actual_wav_report_roundtrip_features_units_and_original_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); left=root/'left.wav';right=root/'right.wav'
            wav(left,220);wav(right,330,gain=.2)
            options=dict(version=VERSION,formants=False,pitch=True,pitch_confirmed=True,loudness=True)
            request=AnalysisRequest(str(left),str(right),'ja', [PhoneSpec('a',phone_index=11,
                reference_start=0,reference_end=1,learner_start=0,learner_end=1,alignment_source='provided')],
                profile_id='ja-JP-standard-v1', normalization=options, qwen_explain=False)
            result=analyze_pronunciation(request)
            self.assertEqual(result.normalization['pitch']['status'],'applied')
            self.assertEqual(result.normalization['loudness']['status'],'applied')
            observations={o.feature:o for o in result.observations}
            for feature,unit in [('pitch_relative_st','st'),('intensity_lufs_adjusted_db','dB'),('rms_lufs_adjusted','amplitude')]:
                self.assertIn(feature,observations)
                self.assertEqual(observations[feature].unit,unit)
                self.assertEqual(observations[feature].phone_index,11)
                self.assertEqual(observations[feature].score,0)
                self.assertEqual(result.reference_phones[0].derived_tracks[feature].unit,unit)
            self.assertAlmostEqual(observations['pitch_relative_st'].reference_value,0,places=2)
            # Whole-recording baseline/Hz difference remains in raw metadata, not discarded.
            self.assertGreater(result.normalization['pitch']['learner_baseline']['baseline_hz'],result.normalization['pitch']['reference_baseline']['baseline_hz']*1.4)
            self.assertEqual(result.reference_phones[0].tracks['pitch'].unit,'Hz')
            write_json(result,root/'result.json')
            parsed=json.loads((root/'result.json').read_text(encoding='utf-8'))
            json.dumps(parsed,allow_nan=False)
            write_textgrid(result,root/'result.TextGrid')
            text=(root/'result.TextGrid').read_text(encoding='utf-8')
            self.assertIn('pitch_relative_st',text)
            self.assertIn('intensity_lufs_adjusted_db',text)
            import parselmouth
            self.assertEqual(parselmouth.read(str(root/'result.TextGrid')).class_name,'TextGrid')
            from PIL import ImageDraw
            original=ImageDraw.ImageDraw.text;labels=[]
            def record(draw,xy,text,*args,**kw):
                labels.append(text);return original(draw,xy,text,*args,**kw)
            with patch.object(ImageDraw.ImageDraw,'text',record):
                self.assertTrue(write_overlay_png(result,root/'result.png'))
            self.assertTrue(any('pitch_relative_st [st]' in label for label in labels))
            self.assertTrue(any('intensity_lufs_adjusted_db [dB]' in label for label in labels))
            self.assertEqual(result.check_statuses['stress_or_pitch_accent'],'not_evaluated')
            self.assertEqual(result.errors,[])

    def test_disabled_and_old_json_still_have_no_prosody_derived_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);wav(root/'a.wav',220);wav(root/'b.wav',330)
            for config in (None,dict(version=VERSION,formants=False,pitch=False,loudness=False)):
                request=AnalysisRequest(str(root/'a.wav'),str(root/'b.wav'),'ja',[PhoneSpec('a',reference_start=0,reference_end=1,
                    learner_start=0,learner_end=1,alignment_source='provided')],profile_id='ja-JP-standard-v1',normalization=config)
                result=analyze_pronunciation(request)
                self.assertFalse(any(p.derived_tracks for p in result.reference_phones))
                self.assertFalse(any('relative_st' in o.feature or 'lufs' in o.feature for o in result.observations))
                if config is None:
                    self.assertNotIn('normalization',result.to_dict())


class ProsodyHostTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'config.json'
        self.app=TutorFormApplication([SelectedObject(4,'ref','Sound',Path('ref.wav')),SelectedObject(9,'learner','Sound',Path('learner.wav'))],self.path)
        self.app.window=Mock()
        self.params=dict(reference='4',learner='9',language='ja',profile_id='ja-JP-standard-v1',phonemes='a',
                         normalization=dict(version=VERSION,formants=False,pitch=True,loudness=True,pitch_confirmed=True,pitch_baseline_mode='independent'))

    def test_native_pitch_import_binding_freeze_and_enabled_preferences(self):
        baseline=fit_pitch_baseline(track([110]*10),settings=PITCH_DEFAULTS,language='ja',reading_id='ja-JP-standard-v1',material=dict(audio_identity='neutral'))
        for role,sound_id in [('reference','4'),('learner','9')]:
            self.app.window.create_file_dialog.return_value=['neutral.wav']
            with patch.dict('sys.modules',webview=SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1))),patch('praat_ai.ui.import_pitch_baseline',return_value=baseline):
                receipt=self.app.rpc('tutor.import_pitch_baseline',dict(role=role,sound_id=sound_id,language='ja',profile_id='ja-JP-standard-v1'))
            self.params['normalization'][role+'_pitch_baseline_id']=receipt['id']
        with patch('praat_ai.ui.read_wav',return_value=SimpleNamespace(duration=1)):
            request=self.app.build_request(self.params)
            changed={**self.params,'normalization':{**self.params['normalization'],'pitch_settings':dict(floor_hz=90,ceiling_hz=600)}}
            with self.assertRaisesRegex(ValueError,'测量范围'):
                self.app.build_request(changed)
            self.app.rpc('tutor.submit',self.params)
        self.assertEqual(request.normalization['reference_pitch_baseline']['baseline_hz'],baseline['baseline_hz'])
        raw=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertTrue(raw['pronunciation']['normalization']['pitch'])
        self.assertTrue(raw['pronunciation']['normalization']['loudness'])
        self.assertNotIn('reference_pitch_baseline',raw['pronunciation']['normalization'])

    def test_invalid_range_rejected_before_analysis_or_model(self):
        self.params['normalization']['pitch_settings']={'floor_hz':600,'ceiling_hz':75}
        with patch('praat_ai.ui.read_wav',return_value=SimpleNamespace(duration=1)),self.assertRaisesRegex(ValueError,'F0'):
            self.app.build_request(self.params)
        self.assertIsNone(self.app.result.request)

    def test_runtime_entry_is_explicit_same_python_and_config_no_install(self):
        with patch('praat_ai.ui.Path.is_file',return_value=True),patch('subprocess.Popen') as launch:
            if __import__('os').name=='nt':
                result=self.app.rpc('tutor.configure_runtime',{})
                self.assertTrue(result['opened'])
                environment=launch.call_args.kwargs['env']
                self.assertEqual(environment['PRAAT_AI_CONFIG_PATH'],str(self.path.resolve()))
                self.assertEqual(environment['PRAAT_AI_PATHS_PYTHON'],__import__('sys').executable)
                self.assertEqual(len(launch.call_args.args[0]),1)


if __name__=='__main__':unittest.main()
