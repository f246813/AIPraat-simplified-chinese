import io
import unittest
import wave
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart
from praat_ai.config import AppConfig
from praat_ai.audio_probe import generate_probe, validate_probe, probe_audio


class ProbeTests(unittest.TestCase):
    def test_probe_initializes_its_phase_and_reaches_the_model(self):
        data,expected=generate_probe();calls=[]
        def response(messages,info):
            calls.append(messages)
            return ModelResponse([TextPart(json.dumps(expected,ensure_ascii=False))])
        cfg=AppConfig();cfg.api.base_url='https://test.invalid/v1';cfg.api.model='fixture';cfg.api.thinking_level='off'
        with tempfile.TemporaryDirectory() as root, patch('praat_ai.audio_probe.generate_probe',return_value=(data,expected)):
            result=probe_audio(cfg,Path(root),model=FunctionModel(response))
        self.assertEqual(result['status'],'verified',result)
        self.assertEqual(len(calls),1)

    def test_synthetic_wav_and_feature_validation(self):
        data, expected = generate_probe()
        with wave.open(io.BytesIO(data)) as audio:
            self.assertEqual(audio.getnchannels(),1)
            self.assertEqual(audio.getframerate(),16000)
            self.assertGreater(audio.getnframes(),16000)
        self.assertTrue(validate_probe(expected, expected))
        self.assertFalse(validate_probe({'count':0,'direction':'unknown'}, expected))


if __name__ == '__main__':
    unittest.main()
