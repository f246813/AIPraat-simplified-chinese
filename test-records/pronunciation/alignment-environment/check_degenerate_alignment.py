"""Re-runs the degenerate-alignment case with the shipped code path.

Same audio and same local espeak wav2vec2 model that produced one CTC frame per phone
(reported confidence 0.71-0.78). After the guard, the alignment must be refused and the
composite must fall back to proportional timing, which the rules already treat as
insufficient evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from praat_ai.config import AlignmentConfig, Wav2Vec2AlignmentConfig
from praat_ai.models import AnalysisRequest, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation

ROOT = Path(__file__).resolve().parent
LEARNER = ROOT/'learner.user.wav'
REFERENCE = ROOT/'ckali.cc0.wav'
PHONES = ['a', 'n', 'a', 't', 'a']
WAV2VEC2 = Wav2Vec2AlignmentConfig(
    enabled=True, model=os.environ['AIPRAAT_TEST_WAV2VEC2_MODEL'],
    device='cpu', sample_rate=16000, blank_token_id=0)

request = AnalysisRequest(str(REFERENCE), str(LEARNER), 'ja',
                          [PhoneSpec(ipa=ipa, phone_index=index + 1) for index, ipa in enumerate(PHONES)],
                          transcript='あなた', profile_id='ja-JP-standard-v1',
                          qwen_explain=False, qwen_vision=False, normalization=None)
result = analyze_pronunciation(request, AlignmentConfig(backend='wav2vec2', wav2vec2=WAV2VEC2))
report = dict(marker='SMOKE-NOT-ACCEPTANCE',
              alignment_source=result.alignment_source,
              alignment_confidence=round(result.alignment_confidence, 3),
              warnings=result.warnings,
              check_statuses=result.check_statuses,
              learner_spans=[[phone.ipa, round(phone.start, 4), round(phone.end, 4), phone.alignment_source,
                              round(phone.alignment_confidence, 3)] for phone in result.learner_phones],
              observations=len(result.observations))
(ROOT/'degenerate-alignment-check.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
