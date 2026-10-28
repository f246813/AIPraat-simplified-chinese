"""Offline normalization-materials gate and batch runner (no network / generated labels).

Validation is a provenance/structure gate, NOT verification that declarations are
true and NOT an accuracy measurement. --analyze is opt-in, uses human boundaries,
local extractors only, and preserves predictions separately from human judgments.
Each analysed case runs the same human boundaries twice (un-normalized and
normalized) and reports descriptive observation counts joined with the human
record; no metric, threshold or accuracy percentage is produced. Exit codes:
0 all cases ready/completed, 1 blocked/unavailable, 2 input/CLI error.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

SCHEMA = 'normalization-materials-v1'
ANNOTATION_SCHEMA = 'normalization-human-phones-v1'
VERSION = 'normalization-v1'
PROTOCOL = 'balanced-monophthong-middle-40-60-three-v1'
# Evaluation targets vs fitting materials. A fitting recording must never also be
# evaluated anywhere in the corpus (speaker-isolated fitting).
TARGET_ROLES = ('target',)
FIT_ROLES = ('calibration', 'pitch_baseline')
ROLES = TARGET_ROLES + FIT_ROLES
CASE_SLOTS = ('reference_target', 'learner_target', 'reference_calibration', 'learner_calibration')
BASELINE_SLOTS = ('reference_pitch_baseline', 'learner_pitch_baseline')
PITCH_MODES = ('utterance', 'independent')
INVENTORIES = {
    'ja-JP-standard-v1': ('ja', ('a', 'i', 'ɯ', 'e', 'o')),
    'en-US-general-v1': ('en', ('i', 'ɪ', 'ɛ', 'æ', 'ɑ', 'ɔ', 'ʊ', 'u', 'ʌ')),
}
SETTINGS = {
    'extractor': 'praat-parselmouth-burg', 'time_step_sec': 0.01,
    'max_number_of_formants': 5.0, 'maximum_formant_hz': 5500.0,
    'window_length_sec': 0.025, 'pre_emphasis_from_hz': 50.0,
    'sampling': 'burg-observation-centres-v2',
}
CONFIG_KEYS = ('language', 'profile_id', 'reading_id', 'normalization_version',
               'protocol', 'measurement_settings', 'normalization_options')
PROSODY_OPTIONS = dict(pitch=True, pitch_confirmed=True,
                       pitch_settings=dict(floor_hz=75, ceiling_hz=600),
                       pitch_baseline_mode='utterance', loudness=True)
# Reviewed prosody protocol: the runner refuses to analyse when the live core drifts from it.
PITCH_PROTOCOL = 'neutral-phrase-median-log2-v1'
PITCH_EXTRACTION = dict(extractor='praat-parselmouth-filtered-autocorrelation', time_step_sec=.01,
                        attenuation_at_top=.03, silence_threshold=.09,
                        octave_suspicion_st=[11, 13], maximum_adjacent_gap_sec=.03)
TARGET_LUFS = -23.0


def normalization_options(configuration, gaps):
    """No implicit prosody opt-in. `independent` pitch requires per-side baseline materials."""
    options = configuration.get('normalization_options', {})
    if not isinstance(options, dict):
        gaps.append('normalization_options must be an object')
        return {}
    if set(options) - set(PROSODY_OPTIONS):
        gaps.append('unknown normalization_options; no ignored flags')
    for key in ('pitch', 'loudness'):
        if key in options and type(options[key]) is not bool:
            gaps.append(f'{key} must be a boolean')
    if options.get('pitch') is True:
        if options.get('pitch_confirmed') is not True:
            gaps.append('pitch requires explicit pitch_confirmed=true')
        if options.get('pitch_settings') != PROSODY_OPTIONS['pitch_settings']:
            gaps.append('pitch settings must match reviewed 75-600 Hz protocol')
        if options.get('pitch_baseline_mode', 'utterance') not in PITCH_MODES:
            gaps.append('pitch_baseline_mode must be utterance or independent')
    return options


def case_slots(mode):
    """Slots a case must bind; independent pitch adds one reviewed neutral phrase per side."""
    return CASE_SLOTS + (BASELINE_SLOTS if mode == 'independent' else ())


def slot_role(slot):
    if slot.endswith('calibration'):
        return 'calibration'
    if slot.endswith('pitch_baseline'):
        return 'pitch_baseline'
    return 'target'

MEDIA = {'.wav', '.flac', '.ogg', '.mp3', '.aac', '.m4a', '.opus', '.textgrid'}
SKIP = {'.git', 'node_modules', '.venv', 'venv', '__pycache__'}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read_json(path):
    def bad_constant(value):
        raise ValueError(f'Non-finite JSON: {value}')
    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=bad_constant, object_pairs_hook=unique_object)


def nonempty(value):
    # Obvious placeholders are missing evidence, not valid provenance/identity.
    placeholders = {'unknown', 'unknown source', 'todo', 'tbd', 'n/a', 'none',
                    '待填', '待补', '未知', '未知来源'}
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in placeholders


def wav_info(path):
    with wave.open(str(path), 'rb') as handle:
        if handle.getcomptype() != 'NONE' or handle.getsampwidth() not in (1, 2, 3, 4):
            raise ValueError('Only uncompressed PCM WAV (8/16/24/32-bit) is accepted')
        channels, rate, width, frames = (handle.getnchannels(), handle.getframerate(),
                                        handle.getsampwidth(), handle.getnframes())
        if min(channels, rate, frames) <= 0:
            raise ValueError('Empty/invalid PCM WAV')
        pcm = hashlib.sha256(f'{channels}:{rate}:{width}:'.encode('ascii'))
        count = 0
        while chunk := handle.readframes(65536):
            count += len(chunk)
            pcm.update(chunk)
        if count != frames * channels * width:
            raise ValueError('Truncated PCM WAV')
    return dict(duration=frames/rate, channels=channels, sample_rate=rate,
                sample_width=width, frames=frames, pcm_sha256=pcm.hexdigest())


def file_ref(value, base, label, gaps):
    if not isinstance(value, dict) or not nonempty(value.get('path')):
        gaps.append(f'{label}: missing path and sha256')
        return None
    path = (base/value['path']).resolve()
    if Path(value['path']).is_absolute() or not path.is_relative_to(base.resolve()):
        gaps.append(f'{label}: path must remain inside manifest directory (including symlinks)')
        return None
    if not path.is_file():
        gaps.append(f'{label}: missing file: {value["path"]}')
        return None
    expected = value.get('sha256')
    if not isinstance(expected, str) or len(expected) != 64 or digest(path) != expected:
        gaps.append(f'{label}: SHA-256 missing or mismatch')
        return None
    return path


def check_annotation(annotation, material, info, gaps):
    if not isinstance(annotation, dict):
        gaps.append('annotation must be an object')
        return []
    if annotation.get('schema_version') != ANNOTATION_SCHEMA:
        gaps.append('annotation schema mismatch')
    for key in ('speaker_id', 'language', 'profile_id', 'reading_id'):
        if annotation.get(key) != material.get(key):
            gaps.append(f'annotation {key} mismatch')
    if annotation.get('audio_sha256') != material.get('audio', {}).get('sha256'):
        gaps.append('annotation not bound to exact audio SHA-256')
    for key in ('annotator', 'reviewed_at'):
        if not nonempty(annotation.get(key)):
            gaps.append(f'annotation missing human {key}')
    try:
        datetime.fromisoformat(annotation.get('reviewed_at', '').replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        gaps.append('annotation reviewed_at must be an ISO date/time')
    if annotation.get('method') not in ('manual', 'human_reviewed'):
        gaps.append('annotation must be manual or explicitly human_reviewed, never auto-only')
    if annotation.get('method') == 'human_reviewed' and not nonempty(annotation.get('original_alignment_source')):
        gaps.append('human_reviewed annotation requires original_alignment_source')
    if annotation.get('label_set') != 'IPA':
        gaps.append('annotation label_set must be IPA (no automatic label conversion)')
    phones = annotation.get('phones')
    if not isinstance(phones, list) or not phones:
        gaps.append('annotation missing manually labelled phone intervals')
        return []
    previous_end = 0.0
    for index, phone in enumerate(phones):
        if not isinstance(phone, dict):
            gaps.append(f'phone {index}: must be object')
            continue
        start, end = phone.get('start'), phone.get('end')
        finite = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                     for v in (start, end))
        if not finite or not (0 <= start < end <= info['duration'] + 1e-7) or start < previous_end:
            gaps.append(f'phone {index}: invalid/overlapping/out-of-audio human interval')
        elif finite:
            previous_end = end
        if not nonempty(phone.get('ipa')):
            gaps.append(f'phone {index}: missing IPA')
        if material.get('role') == 'target':
            if phone.get('judgment') not in ('acceptable', 'local_deviation'):
                gaps.append(f'phone {index}: missing human correctness judgment')
            if not nonempty(phone.get('rationale')):
                gaps.append(f'phone {index}: missing human judgment rationale')
    if material.get('role') == 'pitch_baseline':
        # A neutral phrase is not a vowel calibration: no inventory coverage and no
        # per-phone correctness judgment. It must still be a human-reviewed recording
        # of a declared neutral phrase, never the evaluated utterance.
        if not nonempty(annotation.get('neutral_phrase_text')):
            gaps.append('pitch baseline annotation requires declared neutral_phrase_text')
    return phones


def validate_manifest(path):
    path = Path(path).resolve()
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError('Manifest must be a JSON object')
    global_gaps = []
    if data.get('schema_version') != SCHEMA:
        global_gaps.append('unsupported schema_version')
    configuration = data.get('configuration', {})
    if not isinstance(configuration, dict):
        configuration = {}
    profile = configuration.get('profile_id')
    language, inventory = INVENTORIES.get(profile, ('', ()))
    if not inventory:
        global_gaps.append('profile has no supported fixed vowel calibration protocol')
    if configuration.get('language') != language or not language:
        global_gaps.append('language/profile mismatch; no UK/US or language inference')
    if configuration.get('reading_id') != profile:
        global_gaps.append('reading_id must match the supported fixed profile')
    if configuration.get('normalization_version') != VERSION:
        global_gaps.append('normalization version mismatch')
    if configuration.get('protocol') != PROTOCOL:
        global_gaps.append('calibration protocol mismatch')
    if configuration.get('measurement_settings') != SETTINGS:
        global_gaps.append('measurement settings mismatch')
    options = normalization_options(configuration, global_gaps)
    if not nonempty(data.get('corpus_id')):
        global_gaps.append('missing corpus_id')
    speakers = {}
    for speaker in data.get('speakers', []):
        if not isinstance(speaker, dict) or not nonempty(speaker.get('id')) or speaker['id'] in speakers:
            global_gaps.append('missing/duplicate speaker id')
            continue
        speakers[speaker['id']] = speaker
        if speaker.get('language') != language or not nonempty(speaker.get('provenance')):
            global_gaps.append(f'speaker {speaker["id"]}: language/provenance missing or mismatch')
    materials, material_reports = {}, []
    if not isinstance(data.get('materials'), list):
        raise ValueError('materials must be a list')
    for material in data['materials']:
        if not isinstance(material, dict):
            global_gaps.append('material must be an object')
            continue
        mid = material.get('id')
        if not nonempty(mid) or mid in materials:
            global_gaps.append('missing/duplicate material id')
            continue
        gaps = []
        if material.get('speaker_id') not in speakers:
            gaps.append('unknown speaker_id')
        if material.get('role') not in ROLES:
            gaps.append('role must be target, calibration or pitch_baseline')
        for key in CONFIG_KEYS:
            if material.get(key) != configuration.get(key):
                gaps.append(f'configuration mismatch: {key}')
        source = material.get('source', {})
        if not isinstance(source, dict):
            source = {}
        if source.get('kind') != 'real_recording':
            gaps.append('source must explicitly declare real_recording, not synthetic/unknown')
        for key in ('origin', 'permission', 'recorded_at', 'recording_id'):
            if not nonempty(source.get(key)):
                gaps.append(f'missing source {key}')
        try:
            datetime.fromisoformat(source.get('recorded_at', '').replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            gaps.append('source recorded_at must be an ISO date/time')
        audio = file_ref(material.get('audio'), path.parent, 'audio', gaps)
        labels = file_ref(material.get('annotation'), path.parent, 'annotation', gaps)
        info, phones = {}, []
        if audio:
            try:
                if audio.suffix.lower() != '.wav':
                    raise ValueError('Acceptance audio must be PCM WAV; no implicit conversion')
                info = wav_info(audio)
            except (OSError, ValueError, wave.Error, EOFError) as exc:
                gaps.append(f'audio unavailable: {exc}')
        if labels and info:
            try:
                phones = check_annotation(read_json(labels), material, info, gaps)
            except (OSError, ValueError) as exc:
                gaps.append(f'annotation unreadable: {exc}')
        coverage = dict(Counter(p.get('ipa') for p in phones if isinstance(p, dict) and isinstance(p.get('ipa'), str)))
        if material.get('role') == 'calibration':
            for vowel in inventory:
                if coverage.get(vowel, 0) < 3:
                    gaps.append(f'calibration coverage /{vowel}/: {coverage.get(vowel, 0)}/3')
        report = dict(id=mid, status='blocked' if gaps else 'ready', gaps=gaps,
                      coverage=coverage, audio=info)
        material_reports.append(report)
        materials[mid] = dict(material=material, report=report, phones=phones,
                              audio_path=audio, annotation_path=labels)
    cases = data.get('cases')
    if not isinstance(cases, list):
        raise ValueError('cases must be a list')
    mode = options.get('pitch_baseline_mode', 'utterance')
    slots = case_slots(mode)
    case_reports, seen = [], set()
    if not cases:
        global_gaps.append('no acceptance cases; awaiting user materials')
    for case in cases:
        if not isinstance(case, dict):
            global_gaps.append('case must be an object')
            continue
        cid = case.get('id')
        gaps = list(global_gaps)
        if not nonempty(cid) or cid in seen:
            gaps.append('missing/duplicate case id')
        seen.add(str(cid))
        if mode == 'utterance' and any(key in case for key in BASELINE_SLOTS):
            gaps.append('pitch baselines must not be attached while pitch_baseline_mode is utterance')
        entries = {}
        for slot in slots:
            item = materials.get(case.get(slot))
            if not item:
                gaps.append(f'{slot}: missing material')
                continue
            entries[slot] = item
            if item['material'].get('role') != slot_role(slot):
                gaps.append(f'{slot}: material role mismatch')
            if item['report']['gaps']:
                gaps.append(f'{slot}: material blocked (see material gaps)')
        if len(entries) == len(slots):
            for side in ('reference', 'learner'):
                pairs = [side+'_calibration'] + ([side+'_pitch_baseline'] if mode == 'independent' else [])
                for slot in pairs:
                    if entries[slot]['material'].get('speaker_id') != entries[side+'_target']['material'].get('speaker_id'):
                        gaps.append(f'{side}: {slot} belongs to another speaker')
            if entries['reference_target']['material'].get('speaker_id') == entries['learner_target']['material'].get('speaker_id'):
                gaps.append('reference and learner must identify distinct speakers')
            recording_ids = [item['material'].get('source', {}).get('recording_id') for item in entries.values()]
            if len({i for i in recording_ids if isinstance(i, str) and i.strip()}) != len(slots):
                gaps.append(f'{len(slots)} distinct declared source recording_id values required')
            identities = [item['report']['audio'].get('pcm_sha256') for item in entries.values()]
            if len({i for i in identities if i}) != len(slots):
                gaps.append(f'{len(slots)} independent audio materials required; duplicate PCM or unavailable audio')
            ref, learner = entries['reference_target']['phones'], entries['learner_target']['phones']
            if [p.get('ipa') for p in ref if isinstance(p, dict)] != [p.get('ipa') for p in learner if isinstance(p, dict)]:
                gaps.append('target phone sequences do not match; no automatic alignment/label inference')
            if any(p.get('judgment') != 'acceptable' for p in ref if isinstance(p, dict)):
                gaps.append('reference target requires human acceptable judgments')
        case_reports.append(dict(id=cid, status='blocked' if gaps else 'ready', gaps=gaps,
                                 slots={slot: case.get(slot) for slot in slots}))
    isolation = audit_isolation(materials, case_reports, global_gaps)
    ready = (bool(case_reports) and not global_gaps and all(c['status'] == 'ready' for c in case_reports)
             and all(m['status'] == 'ready' for m in material_reports))
    return dict(schema_version=SCHEMA, manifest_sha256=digest(path),
                status='ready' if ready else 'blocked', global_gaps=global_gaps,
                pitch_baseline_mode=mode,
                provenance_notice='Human/source declarations require external audit; hashes do not prove authenticity.',
                accuracy=dict(status='not_measured', reason='No automatic accuracy claim from manifest or predictions.'),
                isolation=isolation, materials=material_reports, cases=case_reports), data, materials


def audit_isolation(materials, case_reports, gaps):
    """Corpus-level view of which recording fits and which is evaluated.

    Fitting material reused as an evaluated target (or attributed to both sides)
    would silently leak the utterance into its own baseline, so it blocks.
    """
    fit, target, speakers, violations = {}, {}, {}, []
    for report in case_reports:
        for slot, mid in sorted((report.get('slots') or {}).items()):
            item = materials.get(mid)
            if not item:
                continue
            identity = item['report']['audio'].get('pcm_sha256')
            side = slot.split('_')[0]
            if identity:
                bucket = target if slot_role(slot) == 'target' else fit
                bucket.setdefault(identity, []).append((side, slot, mid))
            speaker = item['material'].get('speaker_id')
            entry = speakers.setdefault(speaker, dict(materials=[], target_slots=[], fitting_slots=[], sides=[]))
            if mid not in entry['materials']:
                entry['materials'].append(mid)
            entry['target_slots' if slot_role(slot) == 'target' else 'fitting_slots'].append(slot)
            if side not in entry['sides']:
                entry['sides'].append(side)
    def describe(*groups):
        return '; '.join(','.join(f'{slot}:{mid}' for _side, slot, mid in sorted(uses)) for uses in groups)
    for identity, uses in sorted(fit.items()):
        if identity in target:
            violations.append('same PCM used both to fit and to evaluate: ' + describe(uses, target[identity]))
    for identity, uses in sorted(fit.items()):
        if len({side for side, _slot, _mid in uses}) > 1:
            violations.append('one fitting recording attributed to both speakers: ' + describe(uses))
    gaps.extend(violations)
    return dict(status='violations' if violations else 'audited', violations=violations,
                speakers={key: dict(sorted(value.items())) for key, value in sorted(speakers.items())},
                rules=['a fitting recording (calibration or neutral phrase) is never an evaluated target',
                       'one recording never fits both speakers',
                       'declarations still require external audit; PCM identity detects copies, not splicing'],
                notice='Isolation is checked on PCM content and declared speaker identity, not on speaker verification.')


def observations_by_phone(result, limit):
    """Group rule observations by learner phone index; count the ones that do not match a phone."""
    rows = {}
    outside = 0
    for item in result.get('observations') or []:
        index = item.get('phone_index')
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < limit:
            outside += 1
            continue
        rows.setdefault(index, []).append(item)
    return rows, outside


def compare_paths(raw, normalized, phones):
    """Join the un-normalized and normalized rule observations with the human record.

    Counts are descriptive: nothing here converts an observation into a verdict, and
    no percentage is produced until a metric, gold granularity, coverage minimum and
    disagreement rule are agreed for the corpus.
    """
    raw_by, raw_outside = observations_by_phone(raw, len(phones))
    norm_by, norm_outside = observations_by_phone(normalized, len(phones))
    counts = Counter()
    features = {}
    rows = []
    for index, phone in enumerate(phones):
        judgment = phone.get('judgment')
        if judgment in ('acceptable', 'local_deviation'):
            counts['human_' + judgment] += 1
        raw_items, norm_items = raw_by.get(index, []), norm_by.get(index, [])
        counts['raw_observations'] += len(raw_items)
        counts['normalized_observations'] += len(norm_items)
        raw_features = sorted({item.get('feature') for item in raw_items})
        norm_features = sorted({item.get('feature') for item in norm_items})
        if judgment == 'local_deviation':
            counts['local_deviation_phones'] += 1
            counts['local_deviation_with_raw_observation'] += bool(raw_features)
            counts['local_deviation_with_normalized_observation'] += bool(norm_features)
        for label, items in (('raw', raw_items), ('normalized', norm_items)):
            for item in items:
                entry = features.setdefault(item.get('feature'), dict(raw=0, normalized=0, unit=item.get('unit')))
                entry[label] += 1
                entry.setdefault('units', set()).add(item.get('unit'))
        rows.append(dict(phone_index=index, ipa=phone.get('ipa'), judgment=judgment,
                         rationale=phone.get('rationale'), raw_features=raw_features,
                         normalized_features=norm_features))
    for entry in features.values():
        entry['units'] = sorted(str(unit) for unit in entry['units'])
    counts['observations_outside_phone_range'] = raw_outside + norm_outside
    return dict(method='paired_profile_rules_raw_vs_normalized', counts=dict(counts),
                features={key: features[key] for key in sorted(features)}, phones=rows,
                human_judgments_preserved=True, metric='not_defined',
                notice='Descriptive observation counts only: no threshold, no accuracy or error-rate claim. '
                       'A metric, per-feature gold, coverage minimum and disagreement adjudication must be '
                       'agreed before any percentage is computed from these rows.')


def usability_states(result, options):
    """Applied/unavailable per requested feature. Applicability is not correctness."""
    normalization = result.get('normalization') or {}

    def state_of(value):
        value = value or {}
        return dict(status=value.get('status') or 'not_evaluated', reason=value.get('reason') or '')

    states = {}
    for feature, state in (normalization.get('formants') or {}).items():
        states[feature] = state_of(state)
    for feature in ('pitch', 'loudness'):
        if options.get(feature) is True:
            states[feature] = state_of(normalization.get(feature))
    return states


def run_cases(report, data, materials):
    """Strictly local, only ready cases. No aligner/cloud call, no label generation."""
    if not any(case['status'] == 'ready' for case in report['cases']):
        return  # a blocked manifest must not load extractors or invoke analysis
    try:
        from praat_ai import normalization as core
        from praat_ai import normalization_prosody as prosody
        from praat_ai.audio import extract_phone_tracks, read_wav
        from praat_ai.models import AnalysisRequest, PhoneSpec
        from praat_ai.pronunciation import analyze_pronunciation
        if (core.VERSION != VERSION or core.PROTOCOL != PROTOCOL or core.MEASUREMENT_SETTINGS != SETTINGS
                or any(tuple(core.INVENTORIES.get(k, ())) != v[1] for k, v in INVENTORIES.items())):
            raise ValueError('Live runtime normalization configuration changed; review validator protocol first')
        if (prosody.PITCH_PROTOCOL != PITCH_PROTOCOL or prosody.PITCH_EXTRACTION != PITCH_EXTRACTION
                or prosody.PITCH_DEFAULTS != PROSODY_OPTIONS['pitch_settings']
                or prosody.TARGET_LUFS != TARGET_LUFS):
            raise ValueError('Live prosody protocol changed; review validator protocol before analysing materials')
        import parselmouth  # noqa: F401 -- fail before extracting any case
    except (ImportError, ValueError, AttributeError) as exc:
        for case in report['cases']:
            if case['status'] == 'ready':
                case.update(status='unavailable', gaps=[f'local runtime unavailable: {exc}'])
        report['status'] = 'unavailable'
        return
    config = data['configuration']
    options = normalization_options(config, [])
    mode = options.get('pitch_baseline_mode', 'utterance')
    slots = case_slots(mode)
    by_id = {c['id']: c for c in data['cases'] if isinstance(c, dict) and nonempty(c.get('id'))}
    for case_report in report['cases']:
        if case_report['status'] != 'ready':
            continue
        case = by_id[case_report['id']]
        try:
            for slot in slots:
                item = materials[case[slot]]
                if (digest(item['audio_path']) != item['material']['audio']['sha256']
                        or digest(item['annotation_path']) != item['material']['annotation']['sha256']):
                    raise ValueError(f'{slot}: material changed after validation')
            calibrations = {}
            for side in ('reference', 'learner'):
                item = materials[case[side+'_calibration']]
                specs = [PhoneSpec(ipa=p['ipa'], reference_start=p['start'], reference_end=p['end'],
                                   alignment_source='manual') for p in item['phones']]
                tracks = extract_phone_tracks(item['audio_path'], specs, learner=False)
                snapshot = core.fit_formant_calibration(tracks,
                    speaker_label=item['material']['speaker_id'], language=config['language'],
                    profile_id=config['profile_id'], reading_id=config['reading_id'],
                    material=dict(audio_identity=core.audio_identity(read_wav(item['audio_path'])),
                                  wav_sha256=item['material']['audio']['sha256']))
                if snapshot['status'] != 'applied':
                    raise ValueError(f'{side}: measured calibration not applicable: {snapshot}')
                calibrations[side+'_calibration'] = snapshot
            baselines = {}
            if mode == 'independent':
                for side in ('reference', 'learner'):
                    item = materials[case[side+'_pitch_baseline']]
                    snapshot = prosody.import_pitch_baseline(item['audio_path'], settings=options['pitch_settings'],
                        language=config['language'], reading_id=config['reading_id'],
                        speaker_label=item['material']['speaker_id'])
                    if snapshot.get('status') != 'applied':
                        raise ValueError(f'{side}: neutral-phrase pitch baseline not applicable: {snapshot}')
                    baselines[side+'_pitch_baseline'] = snapshot
            reference = materials[case['reference_target']]
            learner = materials[case['learner_target']]
            specs = [PhoneSpec(ipa=r['ipa'], reference_start=r['start'], reference_end=r['end'],
                               learner_start=l['start'], learner_end=l['end'], alignment_source='manual')
                     for r, l in zip(reference['phones'], learner['phones'])]
            shared = dict(reference_object=str(reference['audio_path']),
                          learner_object=str(learner['audio_path']), language=config['language'],
                          profile_id=config['profile_id'], phonemes=specs, qwen_explain=False, qwen_vision=False)
            # Same human boundaries, same rules: only the normalization layer differs.
            raw = analyze_pronunciation(AnalysisRequest(**shared)).to_dict()
            result = analyze_pronunciation(AnalysisRequest(
                normalization=dict(version=VERSION, confirmed=True, formants=True,
                                   **calibrations, **baselines, **options), **shared)).to_dict()
            json.dumps(raw, allow_nan=False)
            json.dumps(result, allow_nan=False)  # reject non-finite runtime results per case
            case_report['prediction'] = result
            case_report['prediction_raw'] = raw
            case_report['human_judgments'] = learner['phones']  # never overwritten by predictions
            case_report['comparison'] = compare_paths(raw, result, learner['phones'])
            states = usability_states(result, options)
            case_report['usability'] = dict(states=states, usable=all(
                states.get(feature, {}).get('status') == 'applied' for feature in ('f1', 'f2')))
            usable = case_report['usability']['usable']
            for feature in ('pitch', 'loudness'):
                if options.get(feature) is True and states.get(feature, {}).get('status') != 'applied':
                    usable = False
            case_report['usability']['usable'] = usable
            for feature, state in sorted(states.items()):
                if state['status'] != 'applied':
                    case_report['gaps'].append(f'{feature} unavailable: {state}')
            case_report['pitch_scope'] = ('independent neutral-phrase baseline, both sides, reviewed protocol'
                                          if mode == 'independent' else
                                          'utterance only; not a full independent speaker baseline')
            case_report.update(status='completed' if usable else 'unavailable')
            if not usable:
                case_report['gaps'].append('Requested normalization not fully applicable; inspect prediction states')
        except Exception as exc:  # isolate local failures; keep the rest of the batch
            case_report.update(status='unavailable', gaps=[f'{type(exc).__name__}: {exc}'])
    report['usability'] = usability_summary(report['cases'], options)
    report['status'] = ('completed' if not report['global_gaps']
                        and all(m['status'] == 'ready' for m in report['materials'])
                        and report['cases'] and all(c['status'] == 'completed' for c in report['cases']) else 'blocked')


def usability_summary(cases, options):
    """Per-feature applicability across the batch; never a correctness rate."""
    requested = [case for case in cases if case.get('usability')]
    summary = {}
    for feature in ('f1', 'f2', 'f3', 'pitch', 'loudness'):
        applied = unavailable = 0
        reasons = Counter()
        for case in requested:
            state = (case['usability']['states'] or {}).get(feature)
            if state is None:
                continue
            status, reason = state.get('status') or 'not_evaluated', state.get('reason') or ''
            if status == 'applied':
                applied += 1
            else:
                unavailable += 1
                reasons[status + (': ' + reason if reason else '')] += 1
        if applied or unavailable:
            summary[feature] = dict(applied=applied, unavailable=unavailable,
                                    reasons=dict(sorted(reasons.items())))
    return dict(cases=len(cases), analysed=len(requested), features=summary,
                notice='Applicability/applied counts describe whether normalization could be computed, '
                       'not whether any pronunciation judgment is correct.')


def template():
    configuration = dict(language='ja', profile_id='ja-JP-standard-v1', reading_id='ja-JP-standard-v1',
                         normalization_version=VERSION, protocol=PROTOCOL, measurement_settings=SETTINGS.copy(),
                         normalization_options=PROSODY_OPTIONS.copy())
    return dict(schema_version=SCHEMA, corpus_id='', configuration=configuration,
                speakers=[], materials=[], cases=[],
                instructions='Empty template, NOT acceptance evidence. Fill actual source/speakers and four '
                             'independent materials per case. Independent pitch additionally needs one reviewed '
                             'neutral phrase per speaker (six materials per case). Never generate human judgments.',
                material_template=dict(id='', speaker_id='', role='calibration', **configuration,
                    source=dict(kind='', origin='', permission='', recorded_at='', recording_id=''),
                    audio=dict(path='', sha256=''), annotation=dict(path='', sha256='')),
                pitch_baseline_material_template=dict(id='', speaker_id='', role='pitch_baseline', **configuration,
                    source=dict(kind='', origin='', permission='', recorded_at='', recording_id=''),
                    audio=dict(path='', sha256=''), annotation=dict(path='', sha256='')),
                annotation_template=dict(schema_version=ANNOTATION_SCHEMA, audio_sha256='', speaker_id='',
                    language='ja', profile_id=configuration['profile_id'], reading_id=configuration['reading_id'],
                    annotator='', reviewed_at='', method='', label_set='IPA', phones=[],
                    neutral_phrase_text=''),
                case_template=dict(id='', reference_target='', learner_target='',
                                   reference_calibration='', learner_calibration='',
                                   reference_pitch_baseline='', learner_pitch_baseline=''))


def inventory(root):
    import os
    root = Path(root).resolve()
    records = []
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(f for f in folders if f not in SKIP and not (Path(directory)/f).is_symlink())
        for name in sorted(files):
            path = Path(directory)/name
            if path.suffix.lower() not in MEDIA or path.is_symlink():
                continue
            item = dict(path=path.relative_to(root).as_posix(), bytes=path.stat().st_size,
                        sha256=digest(path), acceptance_status='unverified',
                        gaps=['No acceptance manifest binding this file to source, speaker, language, configuration and human judgments.'])
            if path.suffix.lower() == '.wav':
                try:
                    item['audio'] = wav_info(path)
                except (OSError, ValueError, wave.Error, EOFError) as exc:
                    item['audio_unavailable'] = str(exc)
            records.append(item)
    return dict(scope='Repository media inventory only; filenames/TextGrids do not establish real speech correctness.',
                status='awaiting_user_materials', counts=dict(Counter(Path(r['path']).suffix.lower() for r in records)),
                files=records, accuracy=dict(status='not_measured'))


def main(argv=None):
    # Windows redirected consoles may use GBK; evidence/stdout are always UTF-8.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifests', nargs='*', type=Path)
    parser.add_argument('--template', type=Path, help='write an EMPTY template, with no labels or speaker claims')
    parser.add_argument('--inventory', type=Path, help='inventory repository audio/TextGrid candidates (not acceptance)')
    parser.add_argument('--analyze', action='store_true', help='run ready cases locally; never align or generate labels')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if sum((bool(args.manifests), bool(args.template), bool(args.inventory))) != 1:
        parser.error('choose manifests, --template, or --inventory')
    if args.analyze and not args.manifests:
        parser.error('--analyze requires manifests')
    if args.output and args.output.exists():
        parser.error('output exists; refusing to overwrite evidence or user material (choose a new path)')
    if args.template and args.output:
        parser.error('--template writes its own path; do not supply --output')
    if args.template:
        if args.template.exists():
            parser.error('template exists; refusing to overwrite user material')
        args.template.parent.mkdir(parents=True, exist_ok=True)
        args.template.write_text(json.dumps(template(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return 0
    reports = []
    if args.inventory:
        result = inventory(args.inventory)
        code = 0  # inventory completion says nothing about acceptance readiness
    else:
        for path in args.manifests:
            try:
                report, data, materials = validate_manifest(path)
                if args.analyze:
                    run_cases(report, data, materials)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                report = dict(status='invalid', error=f'{type(exc).__name__}: {exc}')
            reports.append(dict(manifest=str(path), **report))
        result = dict(executed_at_utc=datetime.now(timezone.utc).isoformat(), manifests=reports)
        code = 2 if any(r['status'] == 'invalid' for r in reports) else int(any(r['status'] not in ('ready', 'completed') for r in reports))
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload, end='')
    return code


if __name__ == '__main__':
    # Support direct execution without installing the app into a new environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
