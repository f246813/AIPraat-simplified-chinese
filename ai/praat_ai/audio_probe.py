"""Opt-in audio functionality check using random synthetic tones only."""
from __future__ import annotations

import asyncio
import io
import math
import secrets
import struct
import threading
import wave
from pathlib import Path
from typing import Literal

from . import model_capabilities as caps
from .escape_policy import AnalysisState, Budget
from .materials import TaskMaterials
from . import api_diagnostics


def generate_probe():
    count = secrets.choice([2, 3, 4])
    ascending = bool(secrets.randbelow(2))
    frequencies = [400 + 250 * i for i in range(count)]
    if not ascending:
        frequencies.reverse()
    samples = []
    for frequency in frequencies:
        for i in range(6400):
            ramp = min(1.0, i / 160, (6399 - i) / 160)
            samples.append(int(10000 * ramp * math.sin(2 * math.pi * frequency * i / 16000)))
        samples.extend([0] * 2400)
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
    return output.getvalue(), {'count':count, 'direction':'升高' if ascending else '降低'}


def validate_probe(answer, expected):
    return answer.get('count') == expected['count'] and answer.get('direction') == expected['direction']


def probe_audio(config, root: Path, *, cancel=None, model=None):
    from pydantic import BaseModel
    from pydantic_ai import Agent, BinaryContent, PromptedOutput
    from .cloud_agent import CloudSession

    class Answer(BaseModel):
        count: int
        direction: Literal['升高', '降低', '不确定']

    data, expected = generate_probe()
    material = TaskMaterials(root)
    material.audio_path = material.directory / 'probe.wav'
    material.audio_path.write_bytes(data)
    material.source, material.source_kind = '随机合成短音频，仅验证功能', 'synthetic'
    state = AnalysisState('音频基础功能验证', '', budget=Budget(config.api.max_context_tokens, 1000, request_limit=2))
    session = CloudSession(config, state, None, cancel or threading.Event(), None, model, [], material, None, None)
    session.phase_context('audio_probe', '')
    session.settings.setdefault('extra_body', {})['modalities'] = ['text']

    async def run():
        task = None
        try:
            agent = Agent(session.model, output_type=PromptedOutput(Answer), retries=0)
            prompt = '听这段音频：有几个分开的音调？这些音调的频率依次升高还是降低？只报告听到的特征，不确定时写不确定。'
            task = asyncio.create_task(session.drive(agent, [prompt, BinaryContent(data, media_type='audio/wav')]))
            while not task.done():
                if session.cancel.is_set():
                    task.cancel()
                    break
                await asyncio.wait({task}, timeout=0.05)
            answer = await task
            verified = validate_probe(answer.model_dump(), expected)
            return {'status':'verified' if verified else 'unverified',
                    'reason':'基础音频特征校验通过' if verified else '音频特征校验未通过，请查看返回结果'}
        except asyncio.CancelledError:
            return {'status':'cancelled', 'reason':'音频验证已取消'}
        except Exception as error:
            detail = caps.safe_error(error, config.api.api_key)
            status = api_diagnostics.status_code(detail, getattr(error, 'status_code', None))
            return {'status':'unsupported' if caps.is_audio_unsupported(status, detail) else 'unverified',
                    'reason':detail, 'status_code':status}
        finally:
            if hasattr(session.model, 'client'):
                await session.model.client.close()
    try:
        return asyncio.run(run())
    finally:
        material.close()
