import math
from pathlib import Path
import tempfile
import unittest
import numpy as np
import parselmouth

from praat_ai.audio import _basic_tracks, _open_parselmouth, extract_phone_tracks
from praat_ai.models import PhoneSpec
from praat_ai.spectral_analysis import measure, settings


class ExtractionProtocolTests(unittest.TestCase):
    def test_two_tones_measure_power_not_amplitude_and_gain_invariant(self):
        rate = 16000
        t = np.arange(400)/rate
        signal = np.sin(2*np.pi*400*t)+.5*np.sin(2*np.pi*4000*t)
        protocol = settings(rate, dict(window='rectangular'))
        self.assertAlmostEqual(measure(signal, rate, protocol)['high_frequency_ratio'], .2, places=12)
        self.assertAlmostEqual(measure(signal*.02, rate, protocol)['high_frequency_ratio'], .2, places=12)
        self.assertAlmostEqual(measure(signal, rate, settings(rate))['high_frequency_ratio'], .2, delta=.001)

    def test_silence_empty_and_no_high_band_are_distinct(self):
        self.assertEqual(measure(np.zeros(400), 16000, settings(16000))['ratio_reason'], 'silence')
        self.assertEqual(measure(np.zeros(0), 16000, settings(16000))['ratio_reason'], 'empty')
        self.assertEqual(measure(np.ones(50), 3000, settings(3000))['ratio_reason'], 'no_high_band')
        track = _basic_tracks(np.zeros(400), 16000, 0.)
        self.assertEqual(track['rms'].values, [0.])
        self.assertTrue(math.isnan(track['high_frequency_ratio'].values[0]))
        self.assertEqual(track['high_frequency_ratio'].analysis['observations'][0]['reason'], 'silence')

    def test_zero_high_energy_and_dc_nyquist_weights(self):
        protocol = settings(16000, dict(window='rectangular'))
        self.assertEqual(measure(np.ones(400), 16000, protocol)['high_frequency_ratio'], 0.)
        # DC power 1 and Nyquist power .25; neither has the interior-bin factor 2.
        signal = 1+.5*(-1.)**np.arange(400)
        self.assertAlmostEqual(measure(signal, 16000, protocol)['high_frequency_ratio'], .2)

    def test_common_band_and_nonfinite_samples(self):
        self.assertRaises(ValueError, settings, 8000, dict(band_high_hz=5000))
        protocol = settings(16000)
        self.assertEqual(measure(np.array([math.nan]), 16000, protocol)['ratio_reason'], 'nan_samples')
        self.assertEqual(measure(np.array([math.inf]), 16000, protocol)['ratio_reason'], 'infinite_samples')

    def test_actual_f0_observation_times_quality_and_independent_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'tone.wav'
            t = np.arange(16000)/16000
            parselmouth.Sound(.3*np.sin(2*np.pi*220*t), 16000).save(str(path), 'WAV')
            diagnostics = {}
            phone = extract_phone_tracks(path, [PhoneSpec('a', reference_start=.1, reference_end=.9)],
                                         learner=False, diagnostics=diagnostics)[0]
            pitch = phone.tracks['pitch']
            accepted = [v for v in pitch.values if math.isfinite(v)]
            self.assertGreater(len(accepted), 10, pitch.analysis)
            self.assertAlmostEqual(sum(accepted)/len(accepted), 220, delta=1.)
            self.assertTrue(pitch.analysis['observations'])
            self.assertNotEqual(pitch.times, phone.tracks['rms'].times)
            self.assertTrue(all(.1 <= time <= .9 for time in pitch.times))
            self.assertEqual(phone.tracks['f1'].analysis['parameters']['maximum_formant_hz'], 5500)
            from praat_ai.normalization import resolve_measurement_settings
            parameters = resolve_measurement_settings({'measurement_settings': {'reference': {'maximum_formant_hz': 9000}}}, 'reference')
            view, status, _ = _open_parselmouth(path, formant_settings=parameters)
            self.assertEqual(status, 'partial')
            tracks = view.tracks([], scope=(.1, .9))
            self.assertTrue(any(math.isfinite(v) for v in tracks['pitch'].values))
            self.assertEqual(tracks['f1'].analysis['status'], 'extraction_failed')
            self.assertTrue(tracks['intensity'].values)
