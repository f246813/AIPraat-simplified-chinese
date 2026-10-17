"""Real end-to-end check of the tutor-side environment RPC (evidence, not acceptance).

Registers the machine's actual Japanese MFA dictionary/model into a scratch copy of the
config, then calls MfaResourceService.environment() with the real conda launcher. The
probe is not stubbed: it really starts `conda run -n aligner`, and the static resource
checks (language, IPA mapping, phone coverage) run unchanged.

  python check_tutor_environment.py default    # an English preset bound to Japanese resources
  python check_tutor_environment.py declared   # a user-declared Japanese preset

TEMP must point at an ASCII directory for the probe's ascii_temp check; the workspace
path itself contains non-ASCII characters.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from praat_ai.mfa_resources import MfaResourceService

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else 'declared'
ROOT = Path(__file__).resolve().parent
SCRATCH = ROOT / 'config.json'
SOURCE = Path(__file__).resolve().parents[3] / 'ai' / 'ai_config.json'
CONDA = os.environ.get('AIPRAAT_TEST_CONDA') or shutil.which('conda')
if not CONDA:
    raise RuntimeError('Set AIPRAAT_TEST_CONDA to the Conda executable.')
MODELS = Path(os.environ.get('AIPRAAT_TEST_MFA_MODELS', str(Path.home() / 'Documents/MFA/pretrained_models')))
DICTIONARY = MODELS / 'dictionary/japanese_mfa.dict'
ACOUSTIC = MODELS / 'acoustic/japanese_mfa.zip'
# The Japanese lexicon/model label long affricates with a bare length mark; IPA mapping
# needs an explicit one-to-one unit for those four.
SUPPLEMENTAL = {'dzː': 'd͡zː', 'dʑː': 'd͡ʑː', 'tsː': 't͡sː', 'tɕː': 't͡ɕː'}

shutil.copyfile(SOURCE, SCRATCH)
service = MfaResourceService(SCRATCH)
dictionary = next(r['id'] for r in service.add('dictionary', [DICTIONARY])['resources']['dictionary']
                  if r['path'] == str(DICTIONARY))
acoustic = next(r['id'] for r in service.add('acoustic', [ACOUSTIC])['resources']['acoustic']
                if r['path'] == str(ACOUSTIC))
preset = dict(service.list()['presets'][0])
preset.update(dictionary=dictionary, acoustic=acoustic, phone_set='ipa', confirmed=True)
if SCENARIO == 'declared':
    # The model's metadata reports "japanese"; a matching preset needs that exact name
    # plus a supplemental map for the four labels the IPA table cannot split.
    preset.update(language='japanese', phone_map=dict(SUPPLEMENTAL))
launcher = dict(executable='', conda_executable=CONDA, conda_environment='aligner')
report = service.rpc('mfa.environment', {'preset': preset, 'launcher': launcher})
report['marker'] = 'REAL-ENVIRONMENT-NOT-ACCEPTANCE'
report['scenario'] = SCENARIO
(ROOT / f'tutor-environment-{SCENARIO}.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
print(json.dumps(dict(scenario=SCENARIO, ready=report['ready'], summary=report['summary'],
                      blocking=report['blocking'],
                      checks=[(c['id'], c['status'], c['detail']) for c in report['environment']['checks']],
                      resources={k: (v or {}).get('name') for k, v in report['resources'].items()}),
                 ensure_ascii=False, indent=2, allow_nan=False))
