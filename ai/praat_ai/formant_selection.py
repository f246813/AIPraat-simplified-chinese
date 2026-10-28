"""Independent-vowel Burg ceiling candidates and conservative engineering selection."""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from .measurement_protocol import bind_tracks, identity
from .models import FeatureTrack, PhoneFeatureTracks
from .normalization import (INVENTORIES, fit_formant_calibration, load_formant_material,
                            resolve_measurement_settings)

PROTOCOL = 'independent-vowel-burg-candidates-v1'
CEILINGS_HZ = (4500., 5000., 5500., 6000., 6500.)
# Engineering screening of explicitly stable calibration windows, not error thresholds.
QUALITY = dict(minimum_frames=3, minimum_pair_coverage=.8, maximum_jitter_st=1.,
               ceiling_margin_ratio=.95, minimum_score_gap=.15, relative_score_gap=.25)


def evaluate_quality(phones, inventory):
    """Equal token/vowel weight; F3 availability never vetoes F1/F2."""
    tokens = {vowel: [] for vowel in inventory}
    for phone in sorted(phones, key=lambda p: p.start):
        if phone.ipa not in tokens or len(tokens[phone.ipa]) >= 3:
            continue
        lo, hi = (phone.start + f*(phone.end-phone.start) for f in (.4, .6))
        first, second = phone.tracks.get('f1'), phone.tracks.get('f2')
        rows = []
        if first and second and len(first.times) == len(first.values) and len(second.times) == len(second.values):
            f2 = dict(zip(second.times, second.values))
            rows = [(t, a, f2.get(t, math.nan)) for t, a in zip(first.times, first.values) if lo-1e-9 <= t <= hi+1e-9]
        ceiling = first.analysis.get('parameters', {}).get('maximum_formant_hz', math.inf) if first else math.inf
        accepted = [(t, a, b) for t, a, b in rows if math.isfinite(a) and math.isfinite(b)
                    and 0 < a < b < ceiling*QUALITY['ceiling_margin_ratio']]
        coverage = len(accepted)/len(rows) if rows else 0.
        consecutive = [(a, b) for a, b in zip(accepted, accepted[1:])
                       if rows.index(b) == rows.index(a)+1]
        changes = [abs(12*math.log2(b[k]/a[k])) for a, b in consecutive for k in (1, 2)]
        jitter = float(np.median(changes)) if changes else None
        reasons = []
        if len(accepted) < QUALITY['minimum_frames']:
            reasons.append('元音中部有效 F1/F2 帧不足')
        if coverage < QUALITY['minimum_pair_coverage']:
            reasons.append('F1/F2 有效覆盖或频率次序不足')
        if jitter is None or jitter > QUALITY['maximum_jitter_st']:
            reasons.append('中部轨迹连续性或稳定性不足')
        tokens[phone.ipa].append(dict(phone_index=phone.phone_index, scope=[lo, hi],
            observations=len(rows), accepted_frames=len(accepted), pair_coverage=coverage,
            median_adjacent_change_st=jitter, status='eligible' if not reasons else 'uncertain',
            reason='；'.join(reasons)))
    complete = bool(inventory) and all(len(items) == 3 for items in tokens.values())
    eligible = complete and all(t['status'] == 'eligible' for items in tokens.values() for t in items)
    scores = [sum(t['median_adjacent_change_st'] + 10*(1-t['pair_coverage']) for t in items)/3
              for items in tokens.values() if len(items) == 3 and all(t['median_adjacent_change_st'] is not None for t in items)]
    return dict(status='eligible' if eligible else 'uncertain', tokens=tokens,
        score=float(np.mean(scores)) if len(scores) == len(inventory) and scores else None,
        score_definition='mean_over_vowels(mean_over_three_tokens(median_adjacent_F1_F2_change_st + 10 * missing_pair_fraction))',
        reason='' if eligible else '独立元音清单覆盖或中部稳定性不足；不能自动采用该候选')


def decide(candidates):
    ranked = sorted((c for c in candidates if c['quality']['status'] == 'eligible'
                     and c.get('calibration', {}).get('status') == 'applied'),
                    key=lambda c: (c['quality']['score'], c['parameters']['maximum_formant_hz']))
    if not ranked:
        return dict(status='unavailable', recommended_id=None, reason='没有具备完整稳定 F1/F2 证据的候选。')
    if len(ranked) == 1:
        return dict(status='suggested', recommended_id=ranked[0]['id'],
                    reason='只有一个候选满足独立元音覆盖与稳定性条件；这是工程建议，不是共振峰真值确认。')
    best, runner = (c['quality']['score'] for c in ranked[:2])
    gap = runner-best
    required = max(QUALITY['minimum_score_gap'], abs(runner)*QUALITY['relative_score_gap'])
    if gap < required:
        return dict(status='uncertain', recommended_id=None, score_gap=gap, required_gap=required,
                    reason='候选差异不足以可靠区分；保留候选供人工选择，不自动覆盖当前参数。')
    return dict(status='suggested', recommended_id=ranked[0]['id'], score_gap=gap, required_gap=required,
                reason='该候选具备完整稳定 F1/F2 证据，且稳定性分数与次选差异超过工程筛选界限。')


def analyze_candidates(wav_path, textgrid_path, *, speaker_label, language, profile_id,
                       reading_id='', measurement_settings=None, target_ids=(), bandwidth_hz=None, cancel=None):
    import parselmouth
    from parselmouth.praat import call
    from .audio import _burg
    from .forced_alignment import AlignmentCancelled

    if profile_id not in INVENTORIES:
        raise ValueError('当前读音没有独立元音候选分析清单。')
    samples, specs, material = load_formant_material(wav_path, textgrid_path)
    if material['audio_identity'] in target_ids:
        raise ValueError('候选选参不得使用本次任一待测录音；请提供独立元音材料。')
    if not speaker_label.strip():
        raise ValueError('候选分析缺少说话人标签。')
    base = resolve_measurement_settings({'measurement_settings': {'reference': measurement_settings or {}}}, 'reference')
    nyquist = samples.sample_rate/2
    if bandwidth_hz in (None, ''):
        declared = None
    else:
        if isinstance(bandwidth_hz, bool):
            raise ValueError('源录音带宽必须为有效 Hz 数值。')
        declared = float(bandwidth_hz)
        if not math.isfinite(declared) or not 0 < declared <= nyquist:
            raise ValueError('声明带宽须为正数且不超过源录音 Nyquist。')
    cap = min(nyquist, declared) if declared is not None else nyquist
    sound = parselmouth.Sound(str(wav_path))
    if sound.n_channels > 1:
        sound = call(sound, 'Convert to mono')
    source = dict(role='independent_vowel_calibration', audio_id=material['audio_identity'],
                  wav_time_domain=[0., samples.duration], sample_rate_hz=samples.sample_rate,
                  channels=samples.channels, channel_processing='mean_to_mono')
    candidates = []
    for ceiling in sorted(set((*CEILINGS_HZ, base['maximum_formant_hz']))):
        if cancel is not None and cancel.is_set():
            raise AlignmentCancelled('已取消共振峰候选分析。')
        parameters = {**base, 'maximum_formant_hz': ceiling}
        candidate = dict(id=identity(dict(protocol=PROTOCOL, material=material, parameters=parameters)),
            parameters=parameters, quality=dict(status='unavailable', score=None, reason='', tokens={}))
        candidates.append(candidate)
        if ceiling > cap:
            candidate['quality']['reason'] = '候选上限超过源录音 Nyquist 或声明带宽，未提取。'
            continue
        try:
            formant = _burg(sound, parameters)
            tracks = []
            for index, spec in enumerate(specs, 1):
                if spec.ipa not in INVENTORIES[profile_id]:
                    continue
                times = [float(t) for t in formant.xs() if spec.reference_start <= t <= spec.reference_end]
                features = {f'f{number}': FeatureTrack(f'f{number}', times.copy(),
                    [float(formant.get_value_at_time(number, t)) for t in times], 'Hz',
                    analysis=dict(protocol=parameters['sampling'], algorithm=parameters['extractor'], parameters=parameters.copy()))
                    for number in (1, 2, 3)}
                tracks.append(PhoneFeatureTracks(spec.phone_index or index, spec.ipa, spec.reference_start,
                    spec.reference_end, features, alignment_source='calibration_textgrid'))
            bind_tracks(tracks, source)
            candidate['quality'] = evaluate_quality(tracks, INVENTORIES[profile_id])
            candidate['tracks'] = [track.to_dict() for track in tracks]
            candidate['calibration'] = fit_formant_calibration(tracks, speaker_label=speaker_label, language=language,
                profile_id=profile_id, reading_id=reading_id, measurement_settings=parameters, material=material)
        except Exception as error:
            candidate['quality']['reason'] = f'候选提取失败：{type(error).__name__}: {error}'
    result = dict(protocol=PROTOCOL, speaker_label=speaker_label, language=language, profile_id=profile_id,
        reading_id=reading_id or profile_id, base_parameters=base, material=material, source=source,
        engine=dict(parselmouth=parselmouth.__version__, praat=parselmouth.PRAAT_VERSION),
        bandwidth=dict(nyquist_hz=nyquist, declared_hz=declared, effective_cap_hz=cap,
            status='user_declared' if declared is not None else 'source_bandwidth_unknown_beyond_nyquist_limit'),
        quality_parameters=deepcopy(QUALITY), candidates=candidates, decision=decide(candidates))
    result['id'] = identity(result)
    return result


def select_candidate(analysis, candidate_id, *, automatic=False):
    candidate = next((c for c in analysis['candidates'] if c['id'] == candidate_id), None)
    if not candidate or candidate.get('calibration', {}).get('status') != 'applied':
        raise ValueError('该候选缺少有效独立 F1/F2 校准，不能采用。')
    if automatic and analysis['decision'].get('recommended_id') != candidate_id:
        raise ValueError('候选尚不能可靠区分，不能自动采用。')
    calibration = deepcopy(candidate['calibration'])
    calibration['formant_selection'] = dict(protocol=PROTOCOL, analysis_id=analysis['id'],
        selected_candidate_id=candidate_id, source='automatic_engineering_suggestion' if automatic else 'manual_candidate_override',
        decision=deepcopy(analysis['decision']), quality_parameters=deepcopy(analysis['quality_parameters']),
        bandwidth=deepcopy(analysis['bandwidth']), engine=deepcopy(analysis['engine']),
        material=deepcopy(analysis['material']), material_source=deepcopy(analysis['source']),
        candidates=[{key: deepcopy(value) for key, value in c.items() if key != 'calibration'} for c in analysis['candidates']])
    calibration['id'] = identity({key: value for key, value in calibration.items() if key != 'id'})
    return calibration


def public_analysis(analysis):
    return {key: deepcopy(value) for key, value in analysis.items() if key != 'candidates'} | dict(candidates=[
        dict(id=c['id'], parameters=deepcopy(c['parameters']), quality=deepcopy(c['quality']),
             can_apply=c.get('calibration', {}).get('status') == 'applied') for c in analysis['candidates']])
