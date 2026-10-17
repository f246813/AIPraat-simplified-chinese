"""Ten downloaded human recordings, five paired engineering comparisons.

CMU ARCTIC BDL/SLT, original audio and transcripts from festvox.org.
Automatic MFA boundaries; no human pronunciation gold or independent vowel
calibration. This run exercises the shipped analysis, not accuracy measurement.
"""
from __future__ import annotations

from collections import Counter
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT / 'ai'))

import numpy as np
import parselmouth
from parselmouth.praat import call

from praat_ai.audio import read_wav
from praat_ai.models import AnalysisRequest, PhoneSpec
from praat_ai.normalization import VERSION
from praat_ai.phone_labels import map_phone
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.report import write_json, write_textgrid, write_overlay_png


def save(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def prepare():
    downloads = json.loads((ROOT / 'downloads.json').read_text(encoding='utf-8'))
    assert len(downloads) == 10, 'Expected exactly ten complete downloads'
    for speaker in ('bdl', 'slt'):
        text = (ROOT / f'{speaker}-etc-txt.done.data').read_text(encoding='utf-8')
        transcripts = dict(re.findall(r'\(\s*(\S+)\s+"([^"]+)"\s*\)', text))
        for row in downloads:
            if row['speaker'] != speaker:
                continue
            source = ROOT / row['path']
            assert hashlib.sha256(source.read_bytes()).hexdigest() == row['sha256']
            row['transcript'] = transcripts[row['utterance']]
            destination = ROOT / 'corpus' / speaker / (row['id'] + '.wav')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
            destination.with_suffix('.lab').write_text(row['transcript'], encoding='utf-8')
    save(ROOT / 'samples.json', downloads)
    return downloads


def align():
    expected = [ROOT / 'alignment' / speaker / f'{speaker}_arctic_a{i:04d}.TextGrid'
                for speaker in ('bdl', 'slt') for i in range(1, 6)]
    if all(p.is_file() for p in expected):
        print('Using this batch\'s existing ten MFA outputs.', flush=True)
        return
    models = Path.home() / 'Documents/MFA/pretrained_models'
    # This installed Kaldi/OpenFst build cannot write Chinese work paths.
    # Reuse the existing workspace drive mapping, after verifying its target.
    ascii_workspace = Path(os.environ['AIPRAAT_TEST_ASCII_WORKSPACE'])
    if not os.path.samefile(ascii_workspace, PROJECT):
        raise RuntimeError('AIPRAAT_TEST_ASCII_WORKSPACE must refer to this workspace')
    work = Path(tempfile.mkdtemp(prefix='aipraat-public-samples-', dir=ascii_workspace / 'scratch'))
    if not str(work).isascii():
        raise RuntimeError('The native aligner needs an ASCII working path')
    shutil.copytree(ROOT / 'corpus', work / 'corpus')
    (ROOT / 'alignment-workspace.txt').write_text(str(work), encoding='utf-8')
    conda_env = Path.home() / 'miniforge3/envs/aligner'
    command = [str(conda_env / 'Scripts/mfa.exe'), 'align', str(work / 'corpus'),
               str(models / 'dictionary/english_us_arpa.dict'),
               str(models / 'acoustic/english_us_arpa.zip'), str(work / 'alignment'),
               '--output_format', 'long_textgrid', '--clean', '--num_jobs', '1',
               '--beam', '100', '--retry_beam', '400',
               '--temporary_directory', str(work / 'mfa-work')]
    env = dict(os.environ)
    env['CONDA_PREFIX'] = str(conda_env)
    env['PATH'] = os.pathsep.join(str(conda_env / p) for p in ('', 'Library/mingw-w64/bin', 'Library/usr/bin', 'Library/bin', 'Scripts')) + os.pathsep + env.get('PATH', '')
    process_temp = work / 'process-temp'
    process_temp.mkdir(exist_ok=True)
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        env[key] = str(process_temp)
    env['MFA_ROOT_DIR'] = str(work / 'mfa-cache')
    env['NUMBA_CACHE_DIR'] = str(work / 'numba-cache')
    # Existing local DLL payload; do not modify the conda environment.
    env['PYTHONPATH'] = str(PROJECT / 'scratch/mfa-sndfile/wheel')
    save(ROOT / 'alignment-command.json', dict(command=command, pythonpath=env['PYTHONPATH'],
        temp=env['TEMP'], mfa_root=env['MFA_ROOT_DIR'], conda_prefix=env['CONDA_PREFIX']))
    print('Running local MFA on all ten recordings...', flush=True)
    with (ROOT / 'alignment.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, timeout=300)
    if completed.returncode:
        raise RuntimeError(f'MFA exited {completed.returncode}; see alignment.log')
    shutil.copytree(work / 'alignment', ROOT / 'alignment', dirs_exist_ok=True)
    missing = [str(p.relative_to(ROOT)) for p in expected if not p.is_file()]
    if missing:
        raise RuntimeError(f'MFA did not export all ten TextGrids: {missing}')


def intervals(speaker, utterance, folder='alignment'):
    path = ROOT / folder / speaker / f'{speaker}_{utterance}.TextGrid'
    grid = parselmouth.read(str(path))
    tiers = [i for i in range(1, int(call(grid, 'Get number of tiers')) + 1)
             if call(grid, 'Get tier name', i).lower() in ('phones', 'phone')]
    if len(tiers) != 1:
        raise ValueError(f'No unique phone tier in {path.name}')
    tier = tiers[0]
    rows = []
    for i in range(1, int(call(grid, 'Get number of intervals', tier)) + 1):
        raw = call(grid, 'Get label of interval', tier, i).strip()
        if raw.lower() in ('', 'sil', 'sp', 'spn', '<eps>', '<unk>'):
            continue
        rows.append(dict(raw=raw, ipa=map_phone(raw, 'arpabet'),
                         start=float(call(grid, 'Get start time of interval', tier, i)),
                         end=float(call(grid, 'Get end time of interval', tier, i))))
    return rows


def compact(result):
    normal = result.normalization or {}
    return dict(observation_count=len(result.observations),
                observations_by_feature=dict(Counter(o.feature for o in result.observations)),
                checks=result.check_statuses,
                pitch={k: v for k, v in normal.get('pitch', {}).items()
                       if k in ('status', 'reason', 'method', 'observation_count', 'comparison_status', 'comparison_gaps')},
                loudness={k: v for k, v in normal.get('loudness', {}).items()
                          if k in ('status', 'reason', 'method', 'observation_count', 'comparison_status', 'quality',
                                   'reference_baseline', 'learner_baseline', 'gain_db', 'comparison_gaps')},
                formants=normal.get('formants', {}), warnings=result.warnings)


def main():
    downloads = prepare()
    if '--prepare-only' in sys.argv:
        return
    align()
    output = dict(marker='PUBLIC-HUMAN-AUDIO-ENGINEERING-RUN-NOT-ACCURACY',
                  samples=downloads, pairs=[], failures=[],
                  alignment='MFA english_us_arpa, automatic, not human checked',
                  formant_scope='Requested, but independent per-speaker calibration is not supplied; report actual insufficiency.',
                  source='http://festvox.org/cmu_arctic/',
                  roles='BDL is reference A, SLT is comparison B; neither speaker is labelled erroneous.')
    for index in range(1, 6):
        utterance = f'arctic_a{index:04d}'
        print('Analyzing', utterance, flush=True)
        try:
            left, right = intervals('bdl', utterance), intervals('slt', utterance)
            matcher = difflib.SequenceMatcher(None, [r['ipa'] for r in left], [r['ipa'] for r in right], autojunk=False)
            matches = [(b.a + j, b.b + j) for b in matcher.get_matching_blocks() for j in range(b.size)]
            if not matches:
                raise ValueError('No common phone sequence; no fabricated pairing')
            specs = [PhoneSpec(ipa=left[a]['ipa'], label=left[a]['raw'], phone_index=a + 1,
                               reference_start=left[a]['start'], reference_end=left[a]['end'],
                               learner_start=right[b]['start'], learner_end=right[b]['end'],
                               alignment_source='mfa_automatic_unreviewed', alignment_confidence=.85)
                     for a, b in matches]
            pair = dict(id=utterance, reference='bdl_' + utterance, comparison='slt_' + utterance,
                        transcript=next(r['transcript'] for r in downloads if r['utterance'] == utterance),
                        reference_phones=len(left), comparison_phones=len(right), matched_phones=len(matches),
                        unmatched_reference=[r for i, r in enumerate(left) if i not in {a for a, _ in matches}],
                        unmatched_comparison=[r for i, r in enumerate(right) if i not in {b for _, b in matches}])
            for mode in ('raw', 'normalized'):
                norm = (dict(version=VERSION, formants=True, confirmed=True, pitch=True, pitch_confirmed=True,
                             pitch_baseline_mode='utterance', pitch_settings=dict(floor_hz=75, ceiling_hz=600), loudness=True)
                        if mode == 'normalized' else None)
                request = AnalysisRequest(str(ROOT / 'audio' / f'bdl_{utterance}.wav'),
                    str(ROOT / 'audio' / f'slt_{utterance}.wav'), 'en', specs,
                    transcript=pair['transcript'], profile_id='en-US-general-v1',
                    qwen_explain=False, qwen_vision=False, normalization=norm)
                began = time.monotonic()
                result = analyze_pronunciation(request)
                directory = ROOT / 'reports' / utterance / mode
                directory.mkdir(parents=True, exist_ok=True)
                write_json(result, directory / 'report.json')
                write_textgrid(result, directory / 'observations.TextGrid')
                rendered = write_overlay_png(result, directory / 'overlay.png')
                pair[mode] = dict(compact(result), runtime_seconds=round(time.monotonic() - began, 3),
                                  png_generated=bool(rendered), report=str((directory / 'report.json').relative_to(ROOT)))
            output['pairs'].append(pair)
            print(utterance, 'phones', len(matches), 'pitch', pair['normalized']['pitch'].get('status'),
                  'loudness', pair['normalized']['loudness'].get('status'), flush=True)
        except Exception as error:
            output['failures'].append(dict(id=utterance, type=type(error).__name__, message=str(error)))
            print(utterance, type(error).__name__, str(error), flush=True)
        save(ROOT / 'batch-results.json', output)
    print('DONE', len(output['pairs']), 'pairs;', len(output['failures']), 'failed', flush=True)
    return 1 if output['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
