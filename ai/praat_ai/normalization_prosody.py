"""Independent experimental pitch/level observations, not accent/stress scoring.

Full-utterance baselines are fitted once, before per-phone comparisons. There
is no pitch-range standardization, per-phone centering, waveform gain/clipping,
or LUFS claim for short-recording RMS fallbacks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ._vendor.librosa import hz_to_midi
from .models import FeatureTrack, PronunciationError
from . import capabilities
from .normalization import VERSION, audio_identity

PITCH_SOURCE = 'librosa@08e2c0a20d650fe00f10aaf3a1eaa9698ac19641/librosa/core/convert.py:hz_to_midi'
PITCH_DEFAULTS = dict(floor_hz=75.0, ceiling_hz=600.0)
PITCH_PROTOCOL = 'neutral-phrase-median-log2-v1'
PITCH_EXTRACTION = dict(extractor='praat-parselmouth-autocorrelation', time_step_sec=.01,
                        octave_suspicion_st=[11, 13], maximum_adjacent_gap_sec=.03)
TARGET_LUFS = -23.0


def state(status, reason='', **extra):
    return dict(status=status, reason=reason, **extra)


def pitch_settings(options):
    settings = (options or {}).get('pitch_settings', PITCH_DEFAULTS)
    if not isinstance(settings, dict):
        raise ValueError('F0 测量范围必须为对象。')
    try:
        floor, ceiling = float(settings['floor_hz']), float(settings['ceiling_hz'])
    except (KeyError, ValueError, TypeError):
        raise ValueError('请填写有效 F0 测量上下限。') from None
    if not (math.isfinite(floor) and math.isfinite(ceiling) and 0 < floor < ceiling):
        raise ValueError('F0 测量范围需为正数且下限小于上限。')
    return dict(floor_hz=floor, ceiling_hz=ceiling)


def utterance_pitch(path, settings):
    """Native Praat timeline, including all unvoiced/undefined positions."""
    try:
        import parselmouth
        sound = parselmouth.Sound(str(path))
        pitch = sound.to_pitch(time_step=.01, pitch_floor=settings['floor_hz'], pitch_ceiling=settings['ceiling_hz'])
        times = pitch.xs().tolist()
        values = [float(pitch.get_value_at_time(t)) for t in times]
        return FeatureTrack('pitch', times, values, 'Hz')
    except Exception:
        # Material too short / unavailable backend is missing evidence, not 0 Hz.
        return FeatureTrack('pitch', [], [], 'Hz')


def reliable_pitch(track, settings):
    values = np.asarray(track.values, dtype=float).copy()
    times = np.asarray(track.times, dtype=float)
    if times.size != values.size:
        return np.full(values.shape, np.nan), dict(rejected_frames=int(values.size), octave_jump_frames=[])
    values[~np.isfinite(values) | (values < settings['floor_hz']) | (values > settings['ceiling_hz']) | ~np.isfinite(times)] = np.nan
    suspect = set()
    for index in range(1, values.size):
        if (np.isfinite(values[index - 1:index + 1]).all() and 0 < times[index] - times[index - 1] <= .03):
            jump = abs(float(hz_to_midi(values[index]) - hz_to_midi(values[index - 1])))
            if 11 <= jump <= 13:  # Conservative, explicitly reported *suspicion*, not correction.
                suspect.update((index - 1, index))
    for index in suspect:
        values[index] = np.nan
    return values, dict(rejected_frames=int(np.count_nonzero(~np.isfinite(values))), octave_jump_frames=sorted(suspect))


def _runs(times, values):
    runs, current = [], []
    for index, (time, value) in enumerate(zip(times, values)):
        if (math.isfinite(time) and math.isfinite(value) and
                (not current or 0 < time - times[current[-1]] <= .03)):
            current.append(index)
        else:
            if current:
                runs.append(current)
            current = [index] if math.isfinite(time) and math.isfinite(value) else []
    if current:
        runs.append(current)
    return runs


def fit_pitch_baseline(track, *, settings, language='', reading_id='', material=None):
    values, quality = reliable_pitch(track, settings)
    valid = values[np.isfinite(values)]
    runs = _runs(track.times, values)
    result = dict(version=VERSION, protocol=PITCH_PROTOCOL, algorithm_source=PITCH_SOURCE,
        language=language, reading_id=reading_id, measurement_settings=deepcopy(settings),
        extraction_protocol=deepcopy(PITCH_EXTRACTION),
        material=deepcopy(material or {}), valid_frames=int(valid.size), quality=quality,
        baseline_intervals=[[track.times[run[0]], track.times[run[-1]]] for run in runs])
    if not any(len(run) >= 3 for run in runs):
        result.update(state('insufficient_evidence', '缺少至少 3 个连续可靠有声帧；未以零值补音高。'))
    else:
        midi = hz_to_midi(valid)
        baseline = float(np.median(midi))
        result.update(state('applied', baseline_midi=baseline, baseline_hz=float(440 * 2 ** ((baseline - 69) / 12)),
            raw_mean_hz=float(np.mean(valid)), raw_median_hz=float(np.median(valid)), raw_min_hz=float(valid.min()), raw_max_hz=float(valid.max())))
    result['id'] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return result


def import_pitch_baseline(path, *, settings, language, reading_id, speaker_label):
    from .audio import read_wav
    path = Path(path)
    if path.suffix.lower() != '.wav':
        raise ValueError('独立中性语句基线必须为 WAV。')
    sound = read_wav(path)
    result = fit_pitch_baseline(utterance_pitch(path, settings), settings=settings, language=language,
        reading_id=reading_id, material=dict(wav_name=path.name, audio_identity=audio_identity(sound),
            wav_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), speaker_label=speaker_label))
    return result


def _derive(phone, feature, values, unit, times=None):
    name = feature
    phone.derived_tracks[name] = FeatureTrack(name, list(times if times is not None else []),
                                             [float(v) for v in values], unit, minimum_tolerance=0)


def apply_pitch(options, references, learners, sounds, paths, *, language, reading_id):
    if options.get('pitch') is not True:
        return state('disabled', '相对音高观察已关闭。')
    if options.get('pitch_confirmed') is not True:
        return state('insufficient_evidence', '请确认 F0 测量范围与材料跟踪质量；归一化不能修复错误跟踪。')
    settings = pitch_settings(options)
    mode = options.get('pitch_baseline_mode', 'utterance')
    if mode not in {'utterance', 'independent'}:
        return state('insufficient_evidence', '未知音高基线方式。')
    baselines = []
    targets = [audio_identity(s) for s in sounds]
    whole = [utterance_pitch(path, settings) for path in paths]
    for index, role in enumerate(('reference', 'learner')):
        if mode == 'utterance':
            baseline = fit_pitch_baseline(whole[index], settings=settings, language=language, reading_id=reading_id)
        else:
            baseline = deepcopy(options.get(role + '_pitch_baseline'))
            if (not isinstance(baseline, dict) or baseline.get('version') != VERSION
                    or baseline.get('protocol') != PITCH_PROTOCOL or baseline.get('language') != language
                    or baseline.get('reading_id') != reading_id or baseline.get('measurement_settings') != settings
                    or baseline.get('extraction_protocol') != PITCH_EXTRACTION):
                return state('insufficient_evidence', '双方独立中性语句基线缺失或语言/读音/测量范围不匹配。', source=mode)
            material = baseline.get('material')
            if not isinstance(material, dict) or not material.get('audio_identity') or material['audio_identity'] in targets:
                return state('insufficient_evidence', '独立音高基线与待测材料相同或缺少材料标识，请改用“本句”方式或重新导入。', source=mode)
        baselines.append(baseline)
    raw_statistics = {}
    for role, full in zip(('reference', 'learner'), whole):
        finite = np.asarray([v for v in full.values if math.isfinite(v) and v > 0], dtype=float)
        raw_statistics[role] = dict(valid_frames=int(finite.size), mean_hz=float(np.mean(finite)) if finite.size else None,
            median_hz=float(np.median(finite)) if finite.size else None)
    metadata = dict(method='independent_neutral_relative_pitch' if mode == 'independent' else 'utterance_relative_pitch',
                    source=mode, unit='st', feature_id='pitch_relative_st', feature_ids=['pitch_reliable_hz', 'pitch_relative_st'],
                    feature_units={'pitch_reliable_hz':'Hz', 'pitch_relative_st':'st'}, measurement_settings=settings,
                    reference_baseline=baselines[0], learner_baseline=baselines[1], algorithm_source=PITCH_SOURCE,
                    raw_utterance_tracks={role: track.to_dict() for role, track in zip(('reference', 'learner'), whole)},
                    raw_target_statistics=raw_statistics,
                    warning='实验性相对音高观察，不诊断重音、高低音或声调。')
    if any(b.get('status') != 'applied' or not isinstance(b.get('baseline_midi'), (int, float))
           or not math.isfinite(b['baseline_midi']) for b in baselines):
        return state('insufficient_evidence', '一方缺少可靠有声基线，未生成配对相对音高。', **metadata)
    for phones, baseline, full in zip((references, learners), baselines, whole):
        # Whole-recording extraction is preserved in metadata; original phone tracks
        # remain untouched. No phone is fitted as an independent pitch baseline.
        values, quality = reliable_pitch(full, settings)
        for phone in phones:
            indices = [i for i, time in enumerate(full.times) if phone.start <= time <= phone.end]
            times = [full.times[i] for i in indices]
            relative = [float(hz_to_midi(values[i]) - baseline['baseline_midi']) if math.isfinite(values[i]) else math.nan for i in indices]
            _derive(phone, 'pitch_reliable_hz', [values[i] for i in indices], 'Hz', times)
            _derive(phone, 'pitch_relative_st', relative, 'st', times)
        metadata.setdefault('target_quality', []).append(quality)
    available = [sum(sum(math.isfinite(v) for v in p.derived_tracks['pitch_relative_st'].values) for p in phones)
                 for phones in (references, learners)]
    return state('applied' if all(n >= 2 for n in available) else 'insufficient_evidence',
                 '' if all(n >= 2 for n in available) else '待测音段缺少可靠有声帧。', **metadata)


def _active_intervals(phones, duration):
    intervals = []
    for phone in sorted(phones, key=lambda p: p.start):
        if (not (math.isfinite(phone.start) and math.isfinite(phone.end) and 0 <= phone.start < phone.end <= duration)
                or not math.isfinite(phone.alignment_confidence) or phone.alignment_confidence < .7
                or phone.alignment_source in {'proportional', 'ui_equal_estimate'}
                or phone.ipa.strip().lower() in {'', 'sil', 'sp', 'silence', '<eps>', '<unk>'}):
            continue
        if intervals and phone.start <= intervals[-1][1]:
            intervals[-1][1] = max(phone.end, intervals[-1][1])
        else:
            intervals.append([phone.start, phone.end])
    return intervals


def _active_rms(sound, intervals):
    ranges = [(int(start * sound.sample_rate), min(sound.samples.size, int(end * sound.sample_rate))) for start, end in intervals]
    count = sum(end - start for start, end in ranges)
    total = sum(float(np.dot(sound.samples[start:end], sound.samples[start:end])) for start, end in ranges)
    return math.sqrt(total / count) if count and math.isfinite(total) else 0.0


def apply_loudness(options, references, learners, sounds):
    if options.get('loudness') is not True:
        return state('disabled', '响度归一化已关闭。')
    peaks = [float(np.max(np.abs(s.samples))) if s.samples.size and np.isfinite(s.samples).all()
             else 0.0 if not s.samples.size else None for s in sounds]
    quality = [dict(role=role, peak_amplitude=p, possible_existing_clipping=p is not None and p >= .999)
               for role, p in zip(('reference', 'learner'), peaks)]
    if any(not np.isfinite(s.samples).all() for s in sounds):
        return state('insufficient_evidence', '音频含非有限采样，未应用响度归一化。', quality=quality)
    fallback = any(s.duration < .4 for s in sounds)
    if fallback:
        intervals = [_active_intervals(phones, s.duration) for phones, s in zip((references, learners), sounds)]
        rms = [_active_rms(s, ranges) for s, ranges in zip(sounds, intervals)]
        metadata = dict(method='utterance_active_rms', unit='dB', feature_ids=['rms_active_relative_db'],
                        feature_units={'rms_active_relative_db':'dB'},
                        reference_baseline=dict(rms=rms[0], intervals=intervals[0]),
                        learner_baseline=dict(rms=rms[1], intervals=intervals[1]), quality=quality,
                        warning='短录音双方统一采用活动区间 RMS，不是 LUFS；不据单音素的校正均值判断轻重音。')
        if any(not math.isfinite(r) or r <= 0 for r in rms):
            return state('insufficient_evidence', '缺少可靠非静音活动区间或活动 RMS 为零。', **metadata)
        for phones, baseline in zip((references, learners), rms):
            for phone in phones:
                track = phone.tracks.get('rms')
                if track and track.unit == 'amplitude':
                    _derive(phone, 'rms_active_relative_db', [20 * math.log10(v / baseline) if math.isfinite(v) and v > 0 else math.nan for v in track.values], 'dB', track.times)
        return state('applied', **metadata)
    blocker = ''
    try:
        # 版本区间不在本模块写死：capabilities 读 requirements.txt（与安装向导同一份约束），
        # 缺依赖/版本不匹配都说清「缺什么、去哪儿装」。
        blocker = capabilities.blocker('loudness')
        if blocker:
            raise RuntimeError(blocker)
        import pyloudnorm as pyln
        import importlib.metadata
        versions = {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pyloudnorm')}
        import scipy
        if np.__version__ != versions['numpy'] or scipy.__version__ != versions['scipy']:
            raise RuntimeError('运行库已更新但当前进程仍载入旧版本；请关闭并重新打开纠音窗口。')
        # A repair may replace same-version files while this Python process has
        # cached modules. Check the resident implementation too, not just metadata.
        reference_signal = .1 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)
        reference_lufs = float(pyln.Meter(48000, block_size=.4).integrated_loudness(reference_signal))
        if not math.isfinite(reference_lufs) or abs(reference_lufs - (-23.04504759460712)) > 1e-5:
            raise RuntimeError('当前进程响度参考探针失败；请配置运行库并关闭/重新打开纠音窗口。')
        meter = pyln.Meter(sounds[0].sample_rate, block_size=.4)
        lufs = [float((meter if s.sample_rate == sounds[0].sample_rate else pyln.Meter(s.sample_rate, block_size=.4)).integrated_loudness(s.samples)) for s in sounds]
        package_version = versions['pyloudnorm']
    except Exception as exc:
        reason = '响度归一化缺少/无法加载 pyloudnorm 或 SciPy，或测量失败；请在“路径配置 → 配置运行库…”检查。'
        if blocker:
            # 有明确的依赖门控结论时，报告里直接写清缺什么/版本怎么不对，而不是让用户回去猜。
            reason = f'响度归一化未应用：{blocker}'
        return state('insufficient_evidence', reason,
                     dependency_error=f'{type(exc).__name__}: {exc}', method='integrated_lufs_constant_gain', quality=quality,
                     repair_action='path_settings.configure_runtime')
    metadata = dict(method='integrated_lufs_constant_gain', pyloudnorm_version=package_version, dependency_versions=versions,
        resident_probe=dict(scope='synthetic_dependency_self_check', integrated_lufs=reference_lufs), block_size_sec=.4,
        target_lufs=TARGET_LUFS, feature_ids=['intensity_lufs_adjusted_db', 'rms_lufs_adjusted'],
        feature_units={'intensity_lufs_adjusted_db':'dB', 'rms_lufs_adjusted':'amplitude'}, quality=quality,
        reference_baseline=dict(integrated_lufs=lufs[0] if math.isfinite(lufs[0]) else None, interval=[0, sounds[0].duration]),
        learner_baseline=dict(integrated_lufs=lufs[1] if math.isfinite(lufs[1]) else None, interval=[0, sounds[1].duration]),
        warning='整段恒定电平校正；Praat 强度曲线单位仍为 dB，不是逐帧 LUFS。不修复削波、AGC、噪声或麦克风频响。')
    if not all(math.isfinite(v) for v in lufs):
        return state('insufficient_evidence', '整段响度非有限（可能静音或门控无有效块），未应用校正。', **metadata)
    for phones, measured, q in zip((references, learners), lufs, quality):
        gain_db = TARGET_LUFS - measured
        gain = 10 ** (gain_db / 20)
        q.update(gain_db=gain_db, gain_linear=gain, would_clip_if_exported=q['peak_amplitude'] * gain > 1)
        for phone in phones:
            for raw, feature, unit in [('intensity', 'intensity_lufs_adjusted_db', 'dB'), ('rms', 'rms_lufs_adjusted', 'amplitude')]:
                track = phone.tracks.get(raw)
                if track and track.unit == unit:
                    values = [(v + gain_db if raw == 'intensity' else v * gain) if math.isfinite(v) else math.nan for v in track.values]
                    _derive(phone, feature, values, unit, track.times)
    return state('applied', **metadata)


def apply_prosody_normalization(options, references, learners, sounds, paths, *, language, reading_id, applicable):
    if options is None:
        return {}
    if not applicable:
        return {name: state('not_applicable', '旧式统一阈值路径不切换到派生音高或响度。') for name in ('pitch', 'loudness')}
    if options.get('version') != VERSION:
        return {name: state('insufficient_evidence', '归一化版本不匹配。') for name in ('pitch', 'loudness')}
    return dict(pitch=apply_pitch(options, references, learners, sounds, paths, language=language, reading_id=reading_id),
                loudness=apply_loudness(options, references, learners, sounds))


def evaluate_prosody_observations(normalization, references, learners):
    """Match contiguous voiced runs, never DTW a concatenation across gaps."""
    from .pronunciation_rules import _sequence_locations
    from .alignment import dtw_align
    if not normalization or len(references) != len(learners):
        return []
    left_ok, right_ok = _sequence_locations(references), _sequence_locations(learners)
    features = []
    if normalization.get('pitch', {}).get('status') == 'applied':
        features.extend(normalization['pitch'].get('feature_ids', ['pitch_relative_st']))
    if normalization.get('loudness', {}).get('status') == 'applied':
        features.extend(normalization['loudness']['feature_ids'])
    observations = []
    for i, (left, right) in enumerate(zip(references, learners)):
        if not (left_ok[i] and right_ok[i] and left.ipa == right.ipa):
            continue
        for feature in features:
            lt, rt = left.derived_tracks.get(feature), right.derived_tracks.get(feature)
            if not lt or not rt or lt.unit != rt.unit:
                continue
            lruns, rruns = _runs(lt.times, lt.values), _runs(rt.times, rt.values)
            if not lruns or len(lruns) != len(rruns):
                section = 'pitch' if feature.startswith('pitch_') else 'loudness'
                normalization[section].setdefault('comparison_gaps', []).append(dict(phone_index=left.phone_index,
                    feature=feature, reason='有效连续区间缺失或缺口结构不匹配，未跨缺口执行 DTW。'))
                continue  # Do not invent a voiced correspondence across unmatched gaps.
            for lrun, rrun in zip(lruns, rruns):
                if len(lrun) < 2 or len(rrun) < 2:
                    continue
                if max(len(lrun), len(rrun)) > 1000:
                    section = 'pitch' if feature.startswith('pitch_') else 'loudness'
                    normalization[section].setdefault('comparison_gaps', []).append(dict(phone_index=left.phone_index,
                        feature=feature, reason='单音位连续帧超过 1000，跳过无界 DTW；未重采样抹平细节，完整曲线仍保留。'))
                    continue
                lv, rv = [lt.values[j] for j in lrun], [rt.values[j] for j in rrun]
                a, b, distance = dtw_align(lv, rv)
                if not a:
                    continue
                lm, rm = float(np.mean(a)), float(np.mean(b))
                observations.append(PronunciationError(left.phone_index, left.ipa, rt.times[rrun[0]], rt.times[rrun[-1]],
                    feature, 0, 0, '独立观察',
                    f'/{left.ipa}/ {feature}：参考 {lm:.3f}{lt.unit}，学习者 {rm:.3f}{lt.unit}（实验性独立观察；未诊断重音、高低音或声调）。',
                    lm, rm, lt.unit, min(left.alignment_confidence, right.alignment_confidence), issue_type='observation',
                    rule_id='prosody_observation.' + feature, evidence=dict(method='dtw_contiguous_run', normalized_distance=distance,
                        reference_interval=[lt.times[lrun[0]], lt.times[lrun[-1]]], learner_interval=[rt.times[rrun[0]], rt.times[rrun[-1]]],
                        original_phone_interval=[right.start, right.end], reference_valid_frames=len(lv), learner_valid_frames=len(rv),
                        normalization_version=VERSION, diagnostic=False)))
    return observations
