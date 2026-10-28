"""Model metadata + thin request/compaction policy, not a Pi agent runtime.

Metadata subset from @mariozechner/pi-ai 0.73.1, git 781152fc24841dc54b22284514604048ebe5e2c9 (MIT).
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from urllib.parse import urlsplit

from . import qwen
from .config import apply_api_to_qwen

MODELS = [
    dict(id='gpt-4o', provider='openai', contextWindow=128000, maxTokens=16384),
    dict(id='gpt-4o-mini', provider='openai', contextWindow=128000, maxTokens=16384),
    dict(id='gpt-4.1', provider='openai', contextWindow=1047576, maxTokens=32768),
    dict(id='gpt-4.1-mini', provider='openai', contextWindow=1047576, maxTokens=32768),
]


def metadata(config):
    # Names at arbitrary gateways do not prove the upstream model window.
    if (urlsplit(config.base_url).hostname or '').lower() == 'api.openai.com':
        return next((dict(m) for m in MODELS if m['id'] == config.model), None)
    return None


def prepare(config, *, explicit_window=False):
    config = copy.deepcopy(config)
    section = config.api if config.api.enabled else config.qwen
    mode = section.token_mode or ('manual' if section.limit_tokens else 'provider')
    section.token_mode = mode
    model = metadata(section)
    if mode == 'auto':
        if not model:
            raise ValueError('此端点/模型尚无已适配窗口；请使用手动模式填写真实上下文窗口')
        section.max_context_tokens = model['contextWindow']
        section.plan_max_tokens = min(model['maxTokens'], max(128, model['contextWindow'] // 8))
    elif mode == 'provider':
        if model:
            section.max_context_tokens = model['contextWindow']
        elif not explicit_window:
            raise ValueError('服务商决定模式仍需真实窗口才能压缩；请先填写并保存上下文窗口')
    elif mode != 'manual':
        raise ValueError('无效 token 模式')
    if section.plan_max_tokens >= section.max_context_tokens and mode != 'provider':
        raise ValueError('回复上限必须小于上下文窗口')
    section.limit_tokens = mode != 'provider'
    if config.api.enabled:
        apply_api_to_qwen(config)
    return config


def model_settings(section, *, sdk=False, input_cost=0):
    result = {}
    if section.plan_temperature is not None:
        result['temperature'] = section.plan_temperature
    if section.top_p is not None:
        result['top_p'] = section.top_p
    if section.presence_penalty is not None:
        result['presence_penalty'] = section.presence_penalty
    if section.token_mode != 'provider':
        room = section.max_context_tokens - input_cost - 256
        if room < 128:
            raise ValueError('真实上下文余量不足；未发送截断的历史或工具调用')
        result['max_tokens'] = min(section.plan_max_tokens, room)
    if (section.base_url.rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta/openai'
            and section.model.startswith('gemini-3')):
        for key in ('temperature', 'top_p', 'presence_penalty'):
            result.pop(key, None)
    return result


def _metric_targets(metrics, state):
    """这次请求的 timing 该写进哪些 dict：显式 ``metrics``，以及 ``state.metrics``。

    两者常常是**同一个对象**（调用方把 state.metrics 传进来），那就只记一次；
    不是同一份时都写，保持旧调用方（只给 ``metrics``）的行为不变。
    """

    targets: list[dict] = []
    for candidate in (metrics, getattr(state, 'metrics', None) if state is not None else None):
        if isinstance(candidate, dict) and all(candidate is not seen for seen in targets):
            targets.append(candidate)
    return targets


def text_request(config, messages, *, cancel=None, runtime=None, metrics=None, phase='summary', state=None):
    """Shared validation/summary adapter without hidden probe sampling defaults.

    ``state``（可选）把这次真实请求接进**同一份**任务账本：发出去之前检查取消与
    ``state.budget.request_limit``、记一次尝试（失败也计），并把 timing/用量写回
    ``state.metrics``——后续 ``CloudSession`` 就是用这份账本初始化请求数与 token 的。
    传 state 时只在这里加一次 ``state.requests``，别的地方不要重复加。
    """
    if cancel is not None and cancel.is_set():
        raise RuntimeError('已取消请求')
    if not config.api.enabled:
        return qwen.QwenClient(config.qwen).chat(messages, max_tokens=config.qwen.plan_max_tokens,
                                               temperature=config.qwen.plan_temperature)
    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai.usage import RunUsage
    from .cloud_agent import create_model
    from .config import QwenConfig
    targets = _metric_targets(metrics, state)
    if state is not None:
        limit = int(getattr(getattr(state, 'budget', None), 'request_limit', 0) or 0)
        used = int(getattr(state, 'requests', 0) or 0)
        if limit and used >= limit:
            raise RuntimeError('本轮模型请求总预算已用完')
        # 发之前记一次尝试：这次请求真的会发出去，失败也照样算（保守计数）。
        state.requests = used + 1
        request_index = state.requests
    else:
        request_index = len((targets[0].get('request_timings') or [])) + 1 if targets else 1

    async def run(shared_model=None):
        model = shared_model or create_model(config)
        settings = model_settings(config.api, sdk=True, input_cost=qwen.estimate_tokens(json.dumps(messages, ensure_ascii=False)))
        thinking = qwen.thinking_request_fields(QwenConfig(provider='api', base_url=config.api.base_url,
                                                           model=config.api.model, thinking_level=config.api.thinking_level))
        for key, value in thinking.items():
            if key == 'reasoning_effort':
                settings['openai_reasoning_effort'] = value
            else:
                settings.setdefault('extra_body', {})[key] = value
        async def invoke():
            from .session_context import native_history
            from . import cloud_metrics
            timing = dict(request=request_index, phase=phase, attempt=1, retry=False,
                          reason='context_compaction')
            source = targets[0] if targets else {}
            timing.update({key: source.get(key, '') for key in ('session_id', 'task_id', 'context_epoch')})
            started = time.monotonic()
            token = cloud_metrics.current_request.set(timing)
            usage = None
            try:
                # 一次 run 只允许一次请求：不重试、不重置预算（成本不翻倍）。
                result = await Agent(model, output_type=str, retries=0).run(
                    message_history=native_history(messages), model_settings=settings,
                    usage=RunUsage(), usage_limits=UsageLimits(request_limit=1))
                usage = getattr(result, 'usage', None)
                timing['status'] = 'complete'
                return result.output
            finally:
                timing.setdefault('status', 'failed')
                timing['elapsed_seconds'] = time.monotonic()-started
                timing.update(cloud_metrics.normalize_usage(timing.pop('raw_usage', {}), provider_url=config.api.base_url))
                if timing.get('input_tokens_total') is None and usage is not None:
                    # 没有 HTTP 钩子观测（替身模型/非 HTTP 路径）时用 SDK 公开用量补齐。
                    timing['input_tokens_total'] = getattr(usage, 'input_tokens', None)
                    timing['output_tokens'] = getattr(usage, 'output_tokens', None)
                    timing['usage_source'] = ['pydantic_ai_usage']
                cloud_metrics.current_request.reset(token)
                for target in targets:
                    target.setdefault('request_timings', []).append(dict(timing))
        task = asyncio.create_task(invoke())
        try:
            while not task.done():
                if cancel is not None and cancel.is_set():
                    task.cancel()
                    raise RuntimeError('已取消请求')
                await asyncio.wait({task}, timeout=.05)
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if shared_model is None and hasattr(model, 'client'):
                await model.client.close()
    return runtime.run(config, run) if runtime is not None else asyncio.run(run())


def context_estimate(config, history, evidence, text, target='', *, phase=None):
    """Same heuristic as compaction; not tokenizer/billing usage. No requests."""
    from .tools import tool_schemas
    if config.api.enabled:
        from .dialogue_policy import dialogue_kind
        from .cloud_agent import planner_system, report_instructions, dialogue_instructions, AnalysisReport
        from .escape_policy import AnalysisState
        from .skill_registry import load
        from .session_context import PhaseContext
        phase = phase or ('dialogue' if dialogue_kind(text) else 'planner')
        if phase == 'dialogue':
            evidence = []
        else:
            from .session_context import evidence_projection
            evidence = evidence_projection(evidence)
        if phase == 'dialogue':
            instruction = dialogue_instructions(config)
            schemas = ''
        else:
            instruction = planner_system(config) if phase == 'planner' else report_instructions(config, AnalysisState(text, target))
            schemas = json.dumps(tool_schemas() if phase == 'planner' else AnalysisReport.model_json_schema(), ensure_ascii=False)
            skills = PhaseContext()
            load(skills, phase, AnalysisState(text, target))
            instruction += '\n'.join(p.content for m in skills.messages for p in m.parts)
        overhead = qwen.estimate_tokens(instruction + schemas)
    else:
        overhead = qwen.estimate_tokens(qwen.planner_instructions(config.qwen) + json.dumps(tool_schemas(), ensure_ascii=False))
    content = json.dumps(history, ensure_ascii=False) + json.dumps(evidence, ensure_ascii=False) + text + target
    return dict(inputTokens=qwen.estimate_tokens(content) + overhead, overheadTokens=overhead)


def compact(store, sid, config, history, evidence, text, target, *, cancel, emit, requester=text_request,
            phase=None, metrics=None, runtime=None):
    """One real summary pass near the actual window, in ALL token modes.

    Keep recent complete turns, original immutable records and structured evidence.
    Refuse overfull requests when one pass cannot safely fit, rather than silently trim.
    """
    section = config.api if config.api.enabled else config.qwen
    if config.api.enabled:
        from .dialogue_policy import dialogue_kind
        phase = phase or ('dialogue' if dialogue_kind(text) else 'planner')
    if phase == 'dialogue':
        projected_evidence = []
    elif config.api.enabled:
        from .session_context import evidence_projection
        projected_evidence = evidence_projection(evidence)
    else:
        projected_evidence = evidence
    structured = json.dumps(projected_evidence, ensure_ascii=False)
    estimate = context_estimate(config, history, evidence if phase != 'dialogue' else [], text, target, phase=phase)
    overhead, cost = estimate['overheadTokens'], estimate['inputTokens']
    reserve = (section.plan_max_tokens if section.token_mode != 'provider' else max(512, section.max_context_tokens // 10))
    if cost + reserve < section.max_context_tokens * .85:
        return [{k: item[k] for k in ('role', 'content')} for item in history]
    recent = history[-6:]
    older = history[:-6]
    if not older:
        raise ValueError('近期消息/专业证据已接近真实窗口，不能安全压缩；请缩小输入或增加窗口')
    prompt = [dict(role='system', content='压缩对话为忠实摘要。保留原任务目标、指代、用户约束、范围、证据来源和执行事实。不得添加测量数字或将推断升级为测量。工具证据另行保留，不以摘要替代。'),
              *[{k: x[k] for k in ('role', 'content')} for x in older],
              dict(role='user', content='请总结以上较早对话；近期完整对话和专业证据由应用保留。')]
    if qwen.estimate_tokens(json.dumps(prompt, ensure_ascii=False)) + reserve >= section.max_context_tokens:
        raise ValueError('较早历史已超过安全摘要窗口；未裁剪或删除原历史')
    options = dict(runtime=runtime, metrics=metrics) if requester is text_request else {}
    summary = requester(config, prompt, cancel=cancel, **options)
    if cancel.is_set() or not str(summary).strip():
        raise RuntimeError('上下文摘要未完成；原历史保留')
    through = max((x.get('_seq', 0) for x in older), default=0)
    candidate = [dict(role='user', content='较早对话摘要（不是测量证据）：\n' + summary), *[{k: x[k] for k in ('role', 'content')} for x in recent]]
    if qwen.estimate_tokens(json.dumps(candidate, ensure_ascii=False) + structured + text + target) + overhead + reserve >= section.max_context_tokens:
        raise ValueError('本轮真实摘要后仍无法容纳证据和近期消息；未覆盖已存上下文')
    store.summarize(sid, summary, through)
    emit('activity', dict(id='compression-' + str(through), type='compression', text='已完成一轮真实上下文摘要；原始记录和专业证据保留', status='complete'))
    return candidate
