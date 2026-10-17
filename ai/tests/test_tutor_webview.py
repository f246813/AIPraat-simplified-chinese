import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from praat_ai.bridge import SelectedObject
from praat_ai.audio import read_wav
from praat_ai.pronunciation_rules import PROFILES
from praat_ai.ui import TutorFormApplication, show_tutor_form


class TutorWebviewTests(unittest.TestCase):
    def setUp(self):
        self.sounds = [SelectedObject(4, "standard", "Sound", Path("standard.wav")),
                       SelectedObject(9, "learner", "Sound", Path("learner.wav"))]
        self.params = dict(reference="4", learner="9", language="cmn", phonemes="m，a; n",
                           transcript=" hello ", threshold=1.3, explain=False, vision=True)
        self.app = TutorFormApplication(self.sounds)
        self.app.window = Mock()

    def test_submit_preserves_selected_ids_and_analysis_options(self):
        with patch("praat_ai.ui.read_wav", return_value=SimpleNamespace(duration=0.6)):
            self.app.rpc("tutor.submit", self.params)
        request = self.app.result.request
        self.assertEqual((request.reference_object, request.learner_object), (4, 9))
        self.assertEqual([phone.ipa for phone in request.phonemes], ["m", "a", "n"])
        self.assertAlmostEqual(request.phonemes[-1].reference_end, 0.6)
        self.assertEqual(request.transcript, "hello")
        self.assertEqual(request.error_threshold, 1.3)
        self.assertFalse(request.qwen_explain)
        self.assertTrue(request.qwen_vision)
        self.app.window.destroy.assert_called_once()
        with self.assertRaises(ValueError):
            self.app.rpc("tutor.submit", self.params)

    def test_rejected_request_keeps_form_open_and_can_retry(self):
        invalid = [dict(phonemes=" ,； "), dict(reference="unknown"),
                   dict(threshold=float("nan")), dict(threshold=float("inf")),
                   dict(profile_id=PROFILES[0].id, language="cmn")]
        for fields in invalid:
            with self.assertRaises(ValueError):
                self.app.rpc("tutor.submit", {**self.params, **fields})
            self.assertIsNone(self.app.result.request)
            self.app.window.destroy.assert_not_called()
        with patch("praat_ai.ui.read_wav", return_value=SimpleNamespace(duration=1)):
            self.app.rpc("tutor.submit", self.params)
        self.assertIsNotNone(self.app.result.request)

    def test_cancel_returns_no_request_without_reading_audio(self):
        with patch("praat_ai.ui.read_wav") as read:
            self.app.rpc("tutor.cancel", {})
        read.assert_not_called()
        self.assertIsNone(self.app.result.request)
        self.app.window.destroy.assert_called_once()

    def test_text_object_disguised_as_wav_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.wav"
            path.write_text('File type = "ooTextFile"\nObject class = "Sound 2"\n', encoding="utf8")
            with self.assertRaisesRegex(ValueError, "export.wav.*PCM WAV"):
                read_wav(path)

    def test_model_source_uses_effective_config_and_does_not_return_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for enabled, source, name in [(False, "本地模型", "local-fixture"),
                                          (True, "云端 API", "cloud-fixture")]:
                path.write_text(json.dumps(dict(qwen=dict(model="local-fixture"),
                    api=dict(enabled=enabled, base_url="https://example.invalid/v1", model="cloud-fixture",
                             api_key="fixture-secret"), modern=dict(preferences=dict(theme="dark")))), encoding="utf8")
                result = TutorFormApplication(self.sounds, path).bootstrap()
                self.assertEqual(result["model"], dict(source=source, name=name))
                self.assertEqual(result["theme"], "dark")
                self.assertNotIn("fixture-secret", json.dumps(result))

    def test_webview_lifecycle_returns_request_and_closes_asset_server(self):
        window = Mock()
        webview = SimpleNamespace(create_window=Mock(return_value=window))
        def start(callback, **options):
            self.assertEqual(options["gui"], "edgechromium")
            app = webview.create_window.call_args.kwargs["js_api"]._application
            app.rpc("tutor.submit", self.params)
        webview.start = start
        with patch.dict("sys.modules", webview=webview), patch("praat_ai.modern_host.AssetServer") as server, \
             patch("praat_ai.ui.read_wav", return_value=SimpleNamespace(duration=1)):
            server.return_value.url = "http://127.0.0.1:9999/index.html"
            result = show_tutor_form(self.sounds)
            self.assertIn("?window=pronunciation", webview.create_window.call_args.args[1])
            self.assertEqual(result.request.learner_object, 9)
            server.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
