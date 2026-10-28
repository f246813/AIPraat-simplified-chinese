from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np

from .models import FeatureTrack, PhoneFeatureTracks, PhoneSpec


@dataclass(slots=True)
class SoundSamples:
    samples: np.ndarray
    sample_rate: int
    channels: int = 1

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(len(self.samples)) / self.sample_rate


def read_wav(path: str | Path) -> SoundSamples:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            raw = handle.readframes(frame_count)
    except (wave.Error, EOFError) as error:
        raise ValueError(f"无法读取 WAV 音频 {Path(path).name}：文件不是有效的 PCM WAV，"
                         "请检查 Praat 对象导出结果。") from error

    if sample_width == 1:
        values = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        values = (values - 128.0) / 128.0
    elif sample_width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sample_width == 4:
        values = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width * 8} bit")

    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)

    return SoundSamples(samples=values, sample_rate=sample_rate, channels=channels)


def _slice(sound: SoundSamples, start: float, end: float) -> np.ndarray:
    start_index = max(0, int(start * sound.sample_rate))
    end_index = min(len(sound.samples), int(end * sound.sample_rate))
    if end_index <= start_index:
        return np.zeros(0, dtype=np.float64)
    return sound.samples[start_index:end_index]


def _frame_bounds(
    sample_count: int,
    sample_rate: int,
    frame_sec: float = 0.025,
    hop_sec: float = 0.01,
) -> tuple[int, int]:
    frame_size = max(16, int(frame_sec * sample_rate))
    hop_size = max(1, int(hop_sec * sample_rate))
    if sample_count < frame_size:
        frame_size = sample_count
    return frame_size, hop_size


def _basic_tracks(segment, sample_rate, offset, spectral_settings=None):
    from . import spectral_analysis as spectral
    settings = spectral.settings(sample_rate, spectral_settings)
    size, hop = _frame_bounds(len(segment), sample_rate, settings['frame_sec'], settings['hop_sec'])
    times = []
    values = {name: [] for name in ('rms', 'zcr', 'spectral_centroid', 'high_frequency_ratio')}
    observations = {name: [] for name in values}
    for start in range(0, max(1, len(segment)-size+1), hop):
        frame = segment[start:start+size]
        if not frame.size:
            break
        time = offset+(start+frame.size/2)/sample_rate
        times.append(time)
        result = spectral.measure(frame, sample_rate, settings)
        finite = np.all(np.isfinite(frame))
        rms = float(np.sqrt(np.mean(frame*frame))) if finite else math.nan
        zcr = float(np.mean(np.signbit(frame)[1:] != np.signbit(frame)[:-1])) if finite and frame.size > 1 else 0.0 if finite else math.nan
        for name, value, reason in (('rms', rms, '' if finite else 'nonfinite_samples'),
                                    ('zcr', zcr, '' if finite else 'nonfinite_samples'),
                                    ('spectral_centroid', result['spectral_centroid'], result['centroid_reason']),
                                    ('high_frequency_ratio', result['high_frequency_ratio'], result['ratio_reason'])):
            values[name].append(value)
            observations[name].append(dict(time=time, value=value if math.isfinite(value) else None,
                status='accepted' if math.isfinite(value) else 'unavailable', reason=reason))
    units = dict(rms='amplitude', zcr='ratio', spectral_centroid='Hz', high_frequency_ratio='ratio')
    tolerances = dict(rms=.04, zcr=.05, spectral_centroid=500., high_frequency_ratio=0.)
    return {name: FeatureTrack(name, times.copy(), samples, units[name], minimum_tolerance=tolerances[name],
        analysis=dict(protocol=spectral.PROTOCOL,
            algorithm=spectral.ENERGY_ALGORITHM if name == 'high_frequency_ratio' else 'amplitude-centroid-v1' if name == 'spectral_centroid' else name+'-v1',
            parameters={**settings, 'actual_frame_samples': size, 'actual_hop_samples': hop},
            observations=observations[name], reason='' if any(math.isfinite(v) for v in samples) else
                observations[name][0]['reason'] if observations[name] else 'empty',
            threshold_status='uncalibrated' if name == 'high_frequency_ratio' else 'legacy'))
        for name, samples in values.items()}


class _ParselmouthAnalysis:
    """Independent cached analyses with actual observation centres and per-feature status."""
    def __init__(self, sound, pitch, intensity, formant, pitch_settings, formant_settings, statuses, engine):
        self.sound, self.pitch, self.intensity, self.formant = sound, pitch, intensity, formant
        self.pitch_settings, self.formant_settings = pitch_settings, formant_settings
        self.statuses, self.engine = statuses, engine

    def tracks(self, times, scope=None):
        from copy import deepcopy
        from parselmouth.praat import run
        from . import pitch_quality, pitch_events
        lo, hi = scope if scope is not None else (min(times), max(times)) if times else (0., 0.)
        result = {}
        if self.pitch is not None and lo < hi:
            try:
                floor, top = self.pitch_settings['floor_hz'], self.pitch_settings['ceiling_hz']
                script = ['soundId = selected ("Sound")', 'pitchId = selected ("Pitch")', f'tmin = {lo:.12g}', f'tmax = {hi:.12g}',
                          *pitch_quality.screen_lines(sound='soundId', floor=floor, ceiling=top)]
                script.extend(line.replace('appendFileLine: "unused", ', 'appendInfoLine: ')
                    for line in pitch_quality.receipt_lines('"unused"', method='filtered_autocorrelation', floor=floor, ceiling=top))
                _, output = run([self.sound, self.pitch], '\n'.join(script), capture_output=True)
                acoustic, frames = pitch_events.consume(output.splitlines(), {}, {}, 'pitch_statistics')
                result['pitch'] = FeatureTrack('pitch', [f['time'] for f in frames],
                    [f['value'] if f['status'] == 'screened' and f['value'] is not None else math.nan for f in frames],
                    'Hz', .9, 18., analysis={**deepcopy(self.pitch_settings), 'engine': self.engine,
                        'protocol': 'screened-pitch-v1', 'observations': frames, 'checks': acoustic.checks,
                        'reason': '' if any(f['status'] == 'screened' for f in frames) else '没有通过共享质量筛查的 F0 帧'})
            except Exception as error:
                result['pitch'] = FeatureTrack('pitch', [], [], 'Hz', analysis=dict(reason=str(error), status='extraction_failed'))
        for name, obj, number, tolerance in (('intensity', self.intensity, None, 4.),
                                             ('f1', self.formant, 1, 120.), ('f2', self.formant, 2, 180.), ('f3', self.formant, 3, 220.)):
            metadata = dict(engine=self.engine, protocol='burg-observation-centres-v2' if number else 'intensity-observation-centres-v1',
                            parameters=deepcopy(self.formant_settings) if number else dict(minimum_pitch=50., time_step=.01))
            observed_times, observed_values = [], []
            if obj is not None:
                for time in obj.xs():
                    if lo <= time <= hi:
                        observed_times.append(float(time))
                        try:
                            value = float(obj.get_value_at_time(number, time) if number else obj.get_value(time))
                        except Exception:
                            value = math.nan
                        observed_values.append(value if math.isfinite(value) and (not number or value > 0) else math.nan)
            state = self.statuses['formant' if number else 'intensity']
            metadata.update(status=state['status'], reason=state['reason'] or ('' if any(math.isfinite(v) for v in observed_values) else '无有效观察帧'),
                observations=[dict(time=t, value=v if math.isfinite(v) else None,
                    status='accepted' if math.isfinite(v) else 'unavailable', reason='' if math.isfinite(v) else 'undefined_frequency' if number else 'undefined_intensity')
                    for t, v in zip(observed_times, observed_values)])
            result[name] = FeatureTrack(name, observed_times, observed_values, 'Hz' if number else 'dB', minimum_tolerance=tolerance, analysis=metadata)
        if 'pitch' not in result:
            result['pitch'] = FeatureTrack('pitch', [], [], 'Hz', analysis={**self.pitch_settings, **self.statuses['pitch']})
        return result


def _burg(sound, settings):
    if settings['maximum_formant_hz'] > sound.sampling_frequency/2:
        raise ValueError('共振峰上限超过源录音 Nyquist；重采样不能恢复该带宽。')
    return sound.to_formant_burg(time_step=settings['time_step_sec'], max_number_of_formants=settings['max_number_of_formants'],
        maximum_formant=settings['maximum_formant_hz'], window_length=settings['window_length_sec'], pre_emphasis_from=settings['pre_emphasis_from_hz'])


def _open_parselmouth(path, pitch_settings=None, formant_settings=None):
    try:
        import parselmouth
    except ImportError:
        from . import capabilities
        return None, 'dependency_missing', capabilities.blocker('pitch') or '缺少 praat-parselmouth。'
    try:
        from parselmouth.praat import call
        from .pitch_analysis import parselmouth_pitch, settings as pitch_method_settings
        from .normalization import resolve_measurement_settings
        sound = parselmouth.Sound(str(path))
        if sound.n_channels > 1:
            sound = call(sound, 'Convert to mono')
        if sound.duration < .02:
            return None, 'unavailable', f'音频时长 {sound.duration:.4f} 秒不足 0.02 秒。'
        band = pitch_settings or dict(floor_hz=75., ceiling_hz=600.)
        settings = formant_settings or resolve_measurement_settings(None, 'reference')
        statuses, objects = {}, {}
        for key, operation in dict(
            pitch=lambda: parselmouth_pitch(sound, band['floor_hz'], band['ceiling_hz'], step=.01),
            intensity=lambda: sound.to_intensity(minimum_pitch=50., time_step=.01),
            formant=lambda: _burg(sound, settings)).items():
            try:
                objects[key] = operation()
                statuses[key] = dict(status='ok', reason='')
            except Exception as error:
                objects[key] = None
                statuses[key] = dict(status='extraction_failed', reason=f'{type(error).__name__}: {error}')
        state = 'ok' if all(s['status'] == 'ok' for s in statuses.values()) else 'partial' if any(objects.values()) else 'extraction_failed'
        return _ParselmouthAnalysis(sound, objects['pitch'], objects['intensity'], objects['formant'],
            pitch_method_settings(band['floor_hz'], band['ceiling_hz']), settings, statuses,
            dict(parselmouth=parselmouth.__version__, praat=getattr(parselmouth, 'PRAAT_VERSION', 'unknown'))), state, '；'.join(s['reason'] for s in statuses.values() if s['reason'])
    except Exception as error:
        return None, 'extraction_failed', f'{type(error).__name__}: {error}'


def extract_phone_tracks(path, phones, *, learner, pitch_settings=None, diagnostics=None, formant_settings=None, spectral_settings=None):
    sound = read_wav(path)
    analysis, status, reason = _open_parselmouth(path, pitch_settings, formant_settings)
    result = []
    for index, phone in enumerate(phones, 1):
        start = phone.learner_start if learner else phone.reference_start
        end = phone.learner_end if learner else phone.reference_end
        if start is None or end is None:
            continue
        tracks = _basic_tracks(_slice(sound, start, end), sound.sample_rate, start, spectral_settings)
        if analysis is not None:
            tracks.update(analysis.tracks(tracks['rms'].times, scope=(start, end)))
        else:
            for name in ('pitch', 'intensity', 'f1', 'f2', 'f3'):
                tracks[name] = FeatureTrack(name, [], [], 'dB' if name == 'intensity' else 'Hz', analysis=dict(status=status, reason=reason))
        for track in tracks.values():
            track.analysis.update(sample_rate_hz=sound.sample_rate, channels=sound.channels,
                                  channel_processing='mean_to_mono' if sound.channels > 1 else 'mono')
        result.append(PhoneFeatureTracks(phone.phone_index if phone.phone_index is not None else index,
            phone.ipa, start, end, tracks, phone.alignment_confidence, phone.alignment_source))
    if diagnostics is not None:
        diagnostics.update(pitch_formant_status=status, pitch_formant_reason=reason,
                           pitch_formant_extractions=int(analysis is not None))
    return result
