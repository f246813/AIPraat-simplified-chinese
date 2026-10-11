"""Evaluate local decision proposals on verified Praat context, without execution.

Expected answers are used only after inference. All cases receive one identical
tool catalogue; no per-case tool hints, measured results, or gold plans are sent.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import statistics
import sys
import time

AI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI))
from praat_ai import qwen, tools
from praat_ai.config import load_config
from praat_ai.local_decision import decide, decide_native, decide_slots
from praat_ai.modern_execution import ModernExecutor
from praat_ai.server import QwenServerManager, running_model_info
from praat_ai.vram import detect_gpu, select_runtime_profile
from run_backend_scenarios import tool_coverage, write_json


def numbers(value):
    return tuple(float(v.strip()) for v in str(value).split(','))


def score(case, decision, objects, chosen):
    checks = dict(intent=True, object=True, scope=True, tool=True, arguments=True)
    issues = []
    expected = {'rejected':'reject', 'discussion':'explain', 'review':'analyze', 'reuse':'reuse'}.get(case.get('expect'), 'measure')
    if decision['intent'] != expected:
        checks['intent'] = False; issues.append('意图应为 ' + expected)
    actions = decision['actions']
    signatures = [json.dumps(a, sort_keys=True) for a in actions]
    if len(signatures) != len(set(signatures)):
        checks['tool'] = False; issues.append('生成了重复动作')
    if expected in {'reject', 'explain', 'reuse'}:
        if actions:
            checks['tool'] = False; issues.append('该请求不应生成测量动作')
        return checks, issues
    if expected == 'analyze':
        return checks, issues + ['综合分析仅检查路由；分析方案质量需人工审阅']
    if not actions:
        checks['tool'] = False; issues.append('没有生成测量动作')
    coverage = {'evidence': actions}
    missing = tool_coverage(case, coverage)
    if missing:
        checks['tool'] = False; issues.append('缺少目标工具/参数：' + ','.join(missing))
    expected_tools = {s['tool'] for s in case['steps']}
    for action in actions:
        args, name = action['arguments'], action['tool']
        if args.get('object') != chosen.id:
            checks['object'] = False; issues.append('未明确绑定正确原对象')
        if name not in expected_tools and name != 'measure':
            checks['tool'] = False; issues.append('出现未请求工具：' + name)
        try:
            from jsonschema import Draft202012Validator
            schema = tools.tool_parameters(name)
            extras = set(args) - set(schema.get('properties', {}))
            if extras:
                raise ValueError('该工具不接受参数：' + ','.join(sorted(extras)))
            Draft202012Validator(schema).validate(args)
            context = tools.ToolContext(tuple(objects), Path('unused-result'), Path('unused-state'))
            tools.render(name, args, context)  # Validation only; never dispatch.
        except Exception as error:
            checks['arguments'] = False; issues.append('参数/类型校验：' + str(error)[:200])
    explicit = next((s['args'] for s in case['steps'] if 'from' in s['args']), None)
    if explicit:
        if decision['scope'] != 'explicit':
            checks['scope'] = False; issues.append('应识别指定时间范围')
        for action in actions:
            for key in ('from', 'to'):
                if key in explicit and action['arguments'].get(key) != explicit[key]:
                    checks['arguments'] = False; issues.append('指定范围 ' + key + ' 未保留')
    elif any('time' in s['args'] or 'burst' in s['args'] for s in case['steps']):
        if decision['scope'] != 'point':
            checks['scope'] = False; issues.append('应识别时间点/已知地标')
    elif '整段' in case['prompt'] or '整体' in case['prompt']:
        if decision['scope'] != 'whole':
            checks['scope'] = False; issues.append('应识别整段')
        if any('to' in a['arguments'] for a in actions):
            checks['arguments'] = False; issues.append('未提供真实时长时不应猜整段终点')
    elif chosen.selection and any('from' in tools.tool_parameters(s['tool']).get('properties', {}) for s in case['steps']):
        if decision['scope'] != 'selection':
            checks['scope'] = False; issues.append('默认应保留真实编辑器选区')
        for action in actions:
            args = action['arguments']
            if any(k in args for k in ('from', 'to')) and (args.get('from'), args.get('to')) != chosen.selection:
                checks['arguments'] = False; issues.append('选区范围被改写')
    for step in case['steps']:
        matching = [a['arguments'] for a in actions if a['tool'] == step['tool']]
        for key in ('time', 'formant', 'burst', 'voicing'):
            if key not in step['args']:
                continue
            try:
                expected_values = set(numbers(step['args'][key]))
                actual_values = {n for args in matching if key in args for n in numbers(args[key])}
                if actual_values != expected_values:
                    raise ValueError()
            except (TypeError, ValueError):
                checks['arguments'] = False; issues.append('指定参数 ' + key + ' 未完整保留')
        if 'method' in step['args'] and not any(a.get('method') == step['args']['method'] for a in matching):
            checks['arguments'] = False; issues.append('斜率计算 method 不符')
    if case['id'] == '18' and any(k in a['arguments'] for a in actions for k in ('burst', 'voicing')):
        checks['arguments'] = False; issues.append('自动 VOT 虚构了地标时刻')
    return checks, list(dict.fromkeys(issues))


def report(root, results, metadata):
    measured = [r for r in results if r['status'] != 'review']
    counts = {s: sum(r['status'] == s for r in results) for s in ('pass', 'fail', 'review')}
    component = {key: sum(r.get('checks', {}).get(key, False) for r in measured)
                 for key in ('intent', 'object', 'scope', 'tool', 'arguments')}
    times = [r['seconds'] for r in results]
    summary = dict(metadata=metadata, counts=counts, component_pass=component,
                   scored_cases=len(measured), median_seconds=statistics.median(times), cases=results)
    write_json(root / 'summary.json', summary)
    lines = ['# 本地 Qwen 结构化决策实验', '',
             '仅评估计划，不投递 Praat、不验证解释报告。每项同一工具清单，评分预期未送入模型。', '',
             f'结果：{counts}；中位耗时 {summary["median_seconds"]:.3f} 秒。', '',
             '| 编号 | 场景 | 决策检查 | 秒 | 问题 |', '|---|---|---|---|---|']
    lines += [f'| {r["id"]} | {r["group"]} | {r["status"]} | {r["seconds"]:.3f} | {"；".join(r["issues"])} |' for r in results]
    (root / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=AI / 'ai_config.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases')
    parser.add_argument('--object', type=int, default=1)
    parser.add_argument('--cpu', action='store_true')
    parser.add_argument('--format', choices=('json', 'native', 'slots'), default='slots')
    args = parser.parse_args()
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    private = root / 'private-config.json'
    raw = json.loads(args.config.read_text(encoding='utf8'))
    raw.setdefault('api', {}).update(enabled=False, api_key='')
    raw.setdefault('qwen', {}).update(provider='llama.cpp', thinking_level='off', enable_thinking=False,
        vision_when_requested=False, max_context_tokens=4096, request_timeout_sec=90)
    raw.setdefault('server', {})['auto_start'] = True
    write_json(private, raw)
    cfg = load_config(private)
    gpu = None if args.cpu else detect_gpu()
    profile = select_runtime_profile(gpu.free_mb if gpu else None, False)
    profile.context_tokens = 4096
    manager = QwenServerManager(cfg, profile)
    executor = ModernExecutor(root, private)
    results = []
    try:
        started = time.monotonic(); manager.ensure_started()
        metadata = dict(model=running_model_info(cfg.qwen.base_url), profile=profile.to_dict(),
                        startup_seconds=round(time.monotonic() - started, 3), execution=False, format=args.format)
        target = executor.capture_target('查询当前对象')
        original = tools.parse_object_context(target)
        chosen = next(r for r in original if r.id == args.object and r.class_name == 'Sound')
        derived = next(r for r in original if r.class_name == 'Harmonicity' and r.selected)
        write_json(root / 'context.json', [asdict(r) for r in original])
        cases = json.loads((Path(__file__).with_name('backend_scenarios.json')).read_text(encoding='utf8'))
        if args.cases:
            wanted = {v.zfill(2) for v in args.cases.split(',')}; cases = [c for c in cases if c['id'] in wanted]
        for case in cases:
            folder = root / case['id']; folder.mkdir()
            # Same selection setup as the live backend runner. Silent Sound is a
            # context fixture only; no silent measurement is claimed here.
            objects = [replace(r, selected=r.id == chosen.id) for r in original]
            current = chosen
            if case.get('fixture') == 'silence':
                fixture_id = max(r.id for r in objects) + 1
                objects = [replace(r, selected=False) for r in objects]
                current = tools.ObjectRow(fixture_id, 'Sound', 'backend_diagnostic_silence', True)
                objects.append(current)
            text = case['prompt'].format(sound=current.id, derived=derived.id)
            previous = []
            if case.get('followup'):
                prior = next((r for r in results if r['id'] == case['followup']), None)
                # These are proposals, not measurement evidence.
                previous = [dict(request=prior['prompt'], status='plan_only',
                                 actions=prior.get('decision', {}).get('actions', []))] if prior else []
            input_context = [asdict(r) for r in objects]
            write_json(folder / 'input.json', dict(request=text, objects=input_context, previous_evidence=previous))
            started = time.monotonic()
            try:
                client = qwen.QwenClient(cfg.qwen)
                post = client._post
                captured = []
                def record(payload):
                    write_json(folder / 'request.json', payload)
                    response = post(payload)
                    write_json(folder / 'response.json', response)
                    captured.append(response)
                    return response
                client._post = record
                decision, usage = {'native':decide_native, 'json':decide, 'slots':decide_slots}[args.format](
                    client, text, input_context, previous_evidence=previous)
                if captured:
                    usage.update({k: captured[-1][k] for k in ('model', 'usage', 'timings') if k in captured[-1]})
                checks, issues = score(case, decision, objects, current)
                status = 'review' if case.get('expect') == 'review' and all(checks.values()) else 'pass' if all(checks.values()) else 'fail'
                result = dict(decision=decision, usage=usage, checks=checks, issues=issues, status=status)
            except Exception as error:
                result = dict(status='fail', issues=[type(error).__name__ + ': ' + str(error).split('\n')[0]], checks={})
            result.update(id=case['id'], group=case['group'], prompt=text, seconds=round(time.monotonic() - started, 3))
            write_json(folder / 'result.json', result); results.append(result)
            report(root, results, metadata)
            print(case['id'], result['status'], result['seconds'], flush=True)
    finally:
        manager.stop(); private.unlink(missing_ok=True)
        if hasattr(executor, 'close'): executor.close()
    return int(any(r['status'] == 'fail' for r in results))


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    raise SystemExit(main())
