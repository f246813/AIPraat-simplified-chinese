"""Supplement the ten target recordings with eight independent calibration clips.

All phone boundaries/labels come from local MFA, not human review. This is a
provisional public-audio engineering run, not pronunciation accuracy evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import wave

import numpy as np
from parselmouth.praat import call

import run_batch as batch
from praat_ai.audio import read_wav
from praat_ai.models import AnalysisRequest, PhoneSpec
from praat_ai.normalization import import_formant_calibration, VERSION
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.report import write_json, write_textgrid, write_overlay_png

ROOT = batch.ROOT
PROJECT = batch.PROJECT


def align_all(downloads):
    work = Path((ROOT / 'alignment-workspace.txt').read_text(encoding='utf-8').strip())
    assert os.path.samefile(Path(os.environ['AIPRAAT_TEST_ASCII_WORKSPACE']), PROJECT)
    for row in downloads:
        target = work / 'corpus' / row['speaker'] / (row['id'] + '.wav')
        shutil.copyfile(ROOT / row['path'], target)
        target.with_suffix('.lab').write_text(row['transcript'], encoding='utf-8')
    expected = [ROOT / 'alignment-with-calibration' / r['speaker'] / (r['id'] + '.TextGrid') for r in downloads]
    if all(p.exists() for p in expected):
        return
    conda = Path.home() / 'miniforge3/envs/aligner'
    models = Path.home() / 'Documents/MFA/pretrained_models'
    command = [str(conda / 'Scripts/mfa.exe'), 'align', str(work / 'corpus'),
               str(models / 'dictionary/english_us_arpa.dict'), str(models / 'acoustic/english_us_arpa.zip'),
               str(work / 'alignment-all'), '--output_format', 'long_textgrid', '--clean',
               '--num_jobs', '1', '--beam', '100', '--retry_beam', '400',
               '--temporary_directory', str(work / 'mfa-work-all')]
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join(str(conda / p) for p in ('', 'Library/mingw-w64/bin', 'Library/usr/bin', 'Library/bin', 'Scripts')) + os.pathsep + env.get('PATH', '')
    env.update(CONDA_PREFIX=str(conda), PYTHONPATH=str(PROJECT / 'scratch/mfa-sndfile/wheel'),
               MFA_ROOT_DIR=str(work / 'mfa-cache'), NUMBA_CACHE_DIR=str(work / 'numba-cache'))
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        env[key] = str(work / 'process-temp')
    batch.save(ROOT / 'calibration-alignment-command.json', dict(command=command, workspace=str(work)))
    print('Aligning 10 targets + 8 independent calibration recordings...', flush=True)
    with (ROOT / 'calibration-alignment.log').open('w', encoding='utf-8') as out:
        result = subprocess.run(command, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=300)
    if result.returncode:
        raise RuntimeError(f'MFA failed with code {result.returncode}; see calibration-alignment.log')
    shutil.copytree(work / 'alignment-all', ROOT / 'alignment-with-calibration', dirs_exist_ok=True)
    assert all(p.exists() for p in expected)


def fit_speaker(speaker, downloads):
    pieces, intervals, sources, offset = [], [], [], 0.0
    for row in downloads:
        if row['speaker'] != speaker:
            continue
        path = ROOT / row['path']
        sound = read_wav(path)
        assert sound.sample_rate == 16000
        pieces.append(sound.samples)
        for item in batch.intervals(speaker, row['utterance'], 'alignment-with-calibration'):
            intervals.append(dict(ipa=item['ipa'], start=item['start'] + offset, end=item['end'] + offset))
        sources.append(dict(id=row['id'], sha256=row['sha256'], offset_seconds=offset, duration=sound.duration))
        offset += sound.duration
    directory = ROOT / 'calibration'
    directory.mkdir(exist_ok=True)
    wav = directory / (speaker + '_independent.wav')
    samples = np.concatenate(pieces)
    with wave.open(str(wav), 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(np.rint(samples * 32768).astype('<i2').tobytes())
    duration = len(samples) / 16000
    grid = call('Create TextGrid...', 0, duration, 'phones', '')
    boundaries = sorted({v for p in intervals for v in (p['start'], p['end']) if 0 < v < duration})
    for boundary in boundaries:
        call(grid, 'Insert boundary', 1, boundary)
    for p in intervals:
        index = int(call(grid, 'Get interval at time', 1, (p['start'] + p['end']) / 2))
        call(grid, 'Set interval text', 1, index, p['ipa'])
    textgrid = directory / (speaker + '_automatic_phones.TextGrid')
    grid.save(str(textgrid))
    result = import_formant_calibration(wav, textgrid, speaker_label=speaker, language='en',
                                       profile_id='en-US-general-v1')
    batch.save(directory / (speaker + '_fit.json'), dict(calibration=result, sources=sources,
        annotations='automatic MFA labels and boundaries, not human reviewed',
        waveform='concatenation of independent public calibration clips; target clips excluded'))
    print(speaker, 'calibration', {f: s['status'] for f, s in result['formants'].items()}, flush=True)
    for f, s in result['formants'].items():
        if s['status'] != 'applied':
            print(f, s['reason'], flush=True)
    return result


def main():
    downloads = json.loads((ROOT / 'calibration-downloads.json').read_text(encoding='utf-8'))
    assert len(downloads) == 8
    align_all(downloads)
    calibrations = {speaker: fit_speaker(speaker, downloads) for speaker in ('bdl', 'slt')}
    original = json.loads((ROOT / 'batch-results.json').read_text(encoding='utf-8'))
    report = dict(marker='PROVISIONAL-AUTOMATIC-CALIBRATION-NOT-HUMAN-ACCEPTANCE',
                  target_recordings=10, supplementary_calibration_recordings=8,
                  calibrations={s: {f: v['status'] for f, v in c['formants'].items()} for s, c in calibrations.items()},
                  pairs=[], failures=[])
    for pair in original['pairs']:
        utterance = pair['id']
        try:
            left, right = batch.intervals('bdl', utterance), batch.intervals('slt', utterance)
            matcher = batch.difflib.SequenceMatcher(None, [p['ipa'] for p in left], [p['ipa'] for p in right], autojunk=False)
            matches = [(b.a + j, b.b + j) for b in matcher.get_matching_blocks() for j in range(b.size)]
            phones = [PhoneSpec(ipa=left[a]['ipa'], label=left[a]['raw'], phone_index=a + 1,
                reference_start=left[a]['start'], reference_end=left[a]['end'],
                learner_start=right[b]['start'], learner_end=right[b]['end'],
                alignment_confidence=.85, alignment_source='mfa_automatic_unreviewed') for a, b in matches]
            options = dict(version=VERSION, formants=True, confirmed=True,
                reference_calibration=calibrations['bdl'], learner_calibration=calibrations['slt'],
                pitch=True, pitch_confirmed=True, pitch_baseline_mode='utterance',
                pitch_settings=dict(floor_hz=75, ceiling_hz=600), loudness=True)
            request = AnalysisRequest(str(ROOT / 'audio' / f'bdl_{utterance}.wav'),
                str(ROOT / 'audio' / f'slt_{utterance}.wav'), 'en', phones,
                transcript=pair['transcript'], profile_id='en-US-general-v1',
                normalization=options, qwen_explain=False, qwen_vision=False)
            result = analyze_pronunciation(request)
            directory = ROOT / 'reports' / utterance / 'calibrated'
            directory.mkdir(exist_ok=True)
            write_json(result, directory / 'report.json')
            write_textgrid(result, directory / 'observations.TextGrid')
            write_overlay_png(result, directory / 'overlay.png')
            item = dict(id=utterance, matched_phones=len(matches), **batch.compact(result))
            report['pairs'].append(item)
            print(utterance, 'z observations', sum(o.feature.endswith('_lobanov_z') for o in result.observations), flush=True)
        except Exception as error:
            report['failures'].append(dict(id=utterance, message=str(error), type=type(error).__name__))
        batch.save(ROOT / 'batch-results-calibrated.json', report)
    print('CALIBRATED DONE', len(report['pairs']), 'pairs;', len(report['failures']), 'failed', flush=True)
    return bool(report['failures'])


if __name__ == '__main__':
    raise SystemExit(main())
