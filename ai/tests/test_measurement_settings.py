import json
import math
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from praat_ai.normalization import MEASUREMENT_SETTINGS, resolve_measurement_settings, _calibration_reason
from test_normalization import fit, PROFILE
from test_normalization_ui import NormalizationHostTests


class MeasurementSettingTests(unittest.TestCase):
    def test_independent_burg_parameters_and_explicit_default(self):
        options = dict(measurement_settings=dict(reference=dict(maximum_formant_hz=5000), learner=dict(maximum_formant_hz=6000)))
        self.assertEqual(resolve_measurement_settings(options, 'reference')['maximum_formant_hz'], 5000)
        self.assertEqual(resolve_measurement_settings(options, 'learner')['maximum_formant_hz'], 6000)
        self.assertEqual(resolve_measurement_settings(None, 'reference'), MEASUREMENT_SETTINGS)
        snapshot = resolve_measurement_settings(options, 'reference')
        snapshot['maximum_formant_hz'] = 7000
        self.assertEqual(options['measurement_settings']['reference']['maximum_formant_hz'], 5000)

    def test_invalid_or_unknown_parameters_are_rejected(self):
        for invalid in (0, -1, math.nan, math.inf, True, 'bad'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                resolve_measurement_settings(dict(measurement_settings=dict(reference=dict(maximum_formant_hz=invalid))), 'reference')
        for options in (dict(measurement_settings=[]), dict(measurement_settings=dict(other={})),
                        dict(measurement_settings=dict(reference=dict(gender='male')))):
            with self.assertRaises(ValueError):
                resolve_measurement_settings(options, 'reference')

    def test_old_calibration_cannot_be_used_after_parameter_change(self):
        calibration = fit()
        changed = {**MEASUREMENT_SETTINGS, 'maximum_formant_hz': 5000.}
        self.assertIn('设置不匹配', _calibration_reason(calibration, 'ja', PROFILE, '', (), changed))
        calibration['measurement_settings'] = changed
        self.assertEqual(_calibration_reason(calibration, 'ja', PROFILE, '', (), changed), '')

    def test_real_form_rpc_keeps_both_settings_and_invalidates_stale_calibration(self):
        host = NormalizationHostTests()
        host.setUp()
        self.addCleanup(host.doCleanups)
        receipt = host._import('reference')
        host.params['normalization'].update(reference_calibration_id=receipt['id'], measurement_settings=dict(reference=dict(maximum_formant_hz=5000)))
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)), self.assertRaisesRegex(ValueError, '设置已改变'):
            host.app.build_request(host.params)
        host.params['normalization'].pop('reference_calibration_id')
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)):
            request = host.app.build_request(host.params)
        host.app._remember_normalization(request.normalization)
        saved = json.loads(host.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['pronunciation']['normalization']['measurement_settings']['reference']['maximum_formant_hz'], 5000)
        self.assertEqual(saved['pronunciation']['normalization']['measurement_settings']['learner']['maximum_formant_hz'], 5500)
        self.assertEqual(saved['api']['api_key'], 'keep-secret')
