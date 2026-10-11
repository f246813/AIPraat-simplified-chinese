"""28 repeatable scenarios: real Praat batch tools or the desktop's real RPC backend.

The live mode uses current configured model and may consume API credits. It uses
an isolated session database and config copy. It does not launch a React window.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

AI = Path(__file__).resolve().parents[1]
PROJECT = AI.parent
sys.path.insert(0, str(AI))
from praat_ai import tools

CASES_PATH = Path(__file__).with_name('backend_scenarios.json')
UNAVAILABLE = ('无法', 'undefined', '未找到', '没有周期', '不能', '不足以', '失败')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def fixture(path, silent=False):
    import array
    samples = array.array('h', (0 if silent else int(9000 * math.sin(2 * math.pi * 220 * i / 16000))
                                for i in range(32000)))
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(samples.tobytes())


def checks(case, rows):
    text = '\n'.join(rows)
    unavailable = any(word in text.lower() for word in UNAVAILABLE)
    issues = []
    if not rows:
        issues.append('没有返回测量行')
    if case.get('unavailable_required') and not unavailable:
        issues.append('应说明无法计算，而不是输出有效基频')
    if not unavailable:
        issues += [f'结果缺少 {marker}' for marker in case.get('markers', []) if marker not in text]
    return issues


def measurement_status(case, rows, issues):
    if issues:
        return 'fail'
    if not case.get('allow_unavailable') and any(word in '\n'.join(rows).lower() for word in UNAVAILABLE):
        return 'unavailable'  # Valid negative measurement, not a broken backend.
    return 'pass'


def tool_coverage(case, evidence):
    """Accept equivalent table-driven measurements, but require their parameters."""
    entries = evidence.get('evidence', [])
    actual = {e.get('tool') for e in entries}
    parameters = {p.strip() for e in entries if e.get('tool') == 'measure'
                  for p in str(e.get('arguments', {}).get('parameter', '')).split(',')}
    equivalents = {'pitch_statistics': {'mean_pitch', 'minimum_pitch', 'maximum_pitch'},
                   'intensity_statistics': {'mean_intensity', 'maximum_intensity'},
                   'harmonicity_statistics': {'hnr'}, 'spectral_emphasis': {'spectral_emphasis'},
                   'intensity_slope': {'intensity_slope_global'}}
    missing = []
    for step in case.get('steps', []):
        tool, args = step['tool'], step['args']
        if tool == 'measure':
            required = set(args['parameter'].split(','))
            if not required <= parameters:
                missing += sorted(required - parameters)
        elif tool not in actual:
            required = equivalents.get(tool)
            if tool.startswith('formant_'):
                prefix = 'b' if tool == 'formant_bandwidth' else 'f'
                required = {prefix + n for n in str(args.get('formant', 2)).split(',')}
            if not required or not required <= parameters:
                missing.append(tool)
    return missing


def console_text(data):
    encoding = 'utf-16-le' if b'\x00' in data[:100] else 'utf-8'
    return data.decode(encoding, 'replace').strip('\ufeff\r\n ')


def run_tools(case, args, folder):
    if case.get('model_only'):
        return dict(status='needs_live', issues=['该项需要真实模型/多轮上下文'], rows=[], steps=[])
    audio = args.audio
    if case.get('fixture') == 'silence':
        audio = folder / 'silence.wav'; fixture(audio, silent=True)
    prelude = f'Read from file: {tools.quote(audio)}\nRename: "诊断 音频"\n'
    rows = [tools.ObjectRow(1, 'Sound', '诊断 音频', True,
                            *(case.get('selection') or (None, None)))]
    if case.get('fixture') == 'derived':
        prelude += 'To Harmonicity (cc): 0.01, 75, 0.1, 1\nRename: "诊断 HNR"\n'
        rows = [tools.ObjectRow(1, 'Sound', '诊断 音频', False),
                tools.ObjectRow(2, 'Harmonicity', '诊断 HNR', True)]
    result_path, state_path = folder / 'result.tsv', folder / 'state.txt'
    context = tools.ToolContext(tuple(rows), result_path, state_path)
    scripts = []
    try:
        for step in case.get('steps', []):
            scripts.append(tools.render(step['tool'], step['args'], context))
    except tools.ToolError as error:
        return dict(status='pass' if case.get('expect') == 'rejected' else 'fail',
                    issues=[] if case.get('expect') == 'rejected' else [str(error)],
                    rejection=str(error), rows=[], steps=case['steps'])
    if case.get('expect') == 'rejected':
        return dict(status='fail', issues=['无效对象/类型未在执行前拒绝'], rows=[], steps=case['steps'])
    collected, runs, issues = [], [], []
    for index, script in enumerate(scripts):
        result_path.unlink(missing_ok=True); state_path.unlink(missing_ok=True)
        source = folder / f'step-{index}.praat'
        source.write_text(prelude + script, encoding='utf-8')
        proc = subprocess.run([str(args.praat), '--no-pref-files', '--no-plugins', '--FULL-TRUST', '--run', str(source)],
                              capture_output=True, timeout=args.timeout)
        output = console_text(proc.stdout + proc.stderr)
        done = state_path.is_file() and state_path.read_text(encoding='utf-8-sig').strip() == 'done'
        lines = result_path.read_text(encoding='utf-8-sig').splitlines() if result_path.is_file() else []
        runs.append(dict(tool=case['steps'][index]['tool'], script=str(source), returncode=proc.returncode,
                         done=done, console=output, rows=lines))
        collected += lines
        if proc.returncode or not done:
            issues.append('Praat 执行失败: ' + (output or '未收到完成标记'))
    issues += checks(case, collected)
    return dict(status=measurement_status(case, collected, issues), issues=issues, rows=collected, steps=runs)


def report(root, results, mode):
    counts = {s: sum(r['status'] == s for r in results) for s in sorted({r['status'] for r in results})}
    write_json(root / 'summary.json', dict(mode=mode, counts=counts, cases=results))
    title = '真实 Praat 工具检查（未测试模型选工具、原生投递和前端渲染）' if mode == 'tools' else '真实桌面 RPC 后端检查（未测试 React/WebView 渲染）'
    lines = ['# 28 场景后端排查', '', title, '', f'统计：{counts}', '',
             '| 编号 | 场景 | 结果 | 耗时（秒） |', '|---|---|---|---|']
    lines += [f"| {r['id']} | {r['group']} | {r['status']} | {r['seconds']:.2f} |" for r in results]
    for r in results:
        lines += ['', f"## {r['id']} {r['group']}", '', r['prompt'], '',
                  f"结果：{r['status']}；详情：[{r['id']}/result.json]({r['id']}/result.json)"]
        lines += ['- ' + issue for issue in r.get('issues', [])]
        if r.get('rows'):
            lines += ['', '```text', '\n'.join(r['rows']), '```']
    (root / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def live_app(args, root):
    from praat_ai.config import default_config_path
    from praat_ai.modern_app import ModernApplication
    # Preserve actual behavior; any capability correction writes only this copy.
    config_path = args.config or default_config_path()
    copy_path = root / 'private-config.json'
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    if args.provider == 'local':
        raw.setdefault('api', {}).update(enabled=False, api_key='')
        raw.setdefault('qwen', {}).update(vision_when_requested=False, default_mode='text')
        if args.context_tokens:
            raw['qwen']['max_context_tokens'] = args.context_tokens
    elif args.provider == 'api':
        raw.setdefault('api', {})['enabled'] = True
    if args.l2_deadline is not None:
        raw.setdefault('modern', {}).setdefault('workflow', {})['l2_deadline_sec'] = args.l2_deadline
    if args.thinking is not None:
        raw.setdefault('api', {})['thinking_level'] = args.thinking
        if args.provider == 'local':
            raw.setdefault('qwen', {}).update(thinking_level=args.thinking,
                enable_thinking=args.thinking not in {'off', 'auto'})
    write_json(copy_path, raw)
    app = ModernApplication(root / 'sessions', copy_path, allow_cloud=True, configured_cloud=True)
    return app, copy_path


def run_live(case, args, folder, app, sessions):
    from praat_ai import chat, delivery
    from praat_ai.modern_execution import _send, _gate, PRAAT_LOCK
    import threading
    # Unique original Sound; callers can pin it explicitly with --object.
    target = app.executor.capture_target('查询当前对象')
    available = tools.parse_object_context(target)
    sounds = [r for r in available if r.class_name == 'Sound']
    chosen = next((r for r in sounds if r.id == args.object), None) if args.object else (
        next((r for r in sounds if r.selected), None) or (sounds[0] if len(sounds) == 1 else None))
    if chosen is None:
        raise ValueError('没有唯一目标 Sound；请用 --object 指定原对象编号')
    selected = [r.id for r in available if r.selected]
    restore = '\n'.join([f'selectObject: {selected[0]}', *[f'plusObject: {i}' for i in selected[1:]]]) if selected else 'nocheck selectObject: 0'
    def select(script):
        with PRAAT_LOCK:
            _gate(threading.Event())
            ok, _, note, fact = _send(chat.praat_executable(), chat.context_pid(target),
                                     tools._assemble(script.splitlines(), tools.ToolContext(available, chat.result_path(), chat.state_path())),
                                     threading.Event())
            if not ok or fact != delivery.DELIVERED:
                raise RuntimeError(note or '选择操作投递未确认')
    derived = next((r for r in available if r.class_name == 'Harmonicity' and r.selected), None) or next(
        (r for r in available if r.class_name == 'Harmonicity'), None)
    if case.get('fixture') == 'derived' and derived is None:
        return dict(status='needs_fixture', issues=['需要一个 Harmonicity 对象；tools 模式可自动准备'], rows=[])
    current_case = dict(case)
    if case.get('selection'):
        if not chosen.selection:
            return dict(status='needs_fixture', issues=['请先在原 Sound 编辑器圈选一个有宽度的区间'], rows=[])
        current_case['markers'] = [f'{n:.3f}' for n in chosen.selection]
    text = case['prompt'].format(sound=chosen.id, derived=derived.id if derived else 0)
    sid = sessions.get(case.get('followup', ''))
    if not sid:
        if case.get('followup'):
            return dict(status='needs_fixture', issues=['请同时运行前置场景 ' + case['followup']], rows=[])
        sid = app.rpc('sessions.create', dict(title=f"诊断 {case['id']} {case['group']}"))['id']
    sessions[case['id']] = sid
    temporary_sound = None
    try:
        if case.get('fixture') == 'silence':
            select('Create Sound from formula: "backend_diagnostic_silence", 1, 0, 2, 16000, "0"')
            fresh = tools.parse_object_context(app.executor.capture_target('查询当前对象'))
            temporary_sound = next(r for r in fresh if r.id not in {o.id for o in available}
                                   and r.class_name == 'Sound' and 'backend_diagnostic_silence' in r.name)
            chosen = temporary_sound
            text = case['prompt'].format(sound=chosen.id, derived=0)
        if not case.get('followup') and case.get('expect') != 'discussion':
            select(f'selectObject: {chosen.id}')
        try:
            task = app.rpc('tasks.submit', dict(sessionId=sid, text=text))
        except Exception as error:
            rejection = app.store.clean(str(error))
            if case.get('expect') == 'rejected' and any(w in rejection for w in ('原目标不在','不存在','类型','不支持')):
                return dict(status='pass', issues=[], rows=[], rejection=rejection, submitted_text=text,
                            delivery='not_submitted')
            raise
        deadline = time.monotonic() + args.timeout
        cursor, events = 0, []
        while app.tasks[task['id']]['thread'].is_alive():
            poll = app.rpc('events.poll', dict(after=cursor))
            events += [e for e in poll['events'] if e['taskId'] == task['id']]
            cursor = poll['cursor']
            if time.monotonic() > deadline:
                app.rpc('tasks.cancel', dict(taskId=task['id']))
                app.tasks[task['id']]['thread'].join(timeout=10)
                if app.tasks[task['id']]['thread'].is_alive():
                    raise RuntimeError('取消尚未完成，停止整个批次以免并发投递')
                return dict(status='fail', issues=['任务超时并取消'], rows=[], events=events)
            time.sleep(.1)
        events += [e for e in app.rpc('events.poll', dict(after=cursor))['events'] if e['taskId'] == task['id']]
        message = app.tasks[task['id']]['message']
        try:
            evidence = app.store.task_evidence(sid, task['id']) or {}
        except ValueError as error:
            if str(error) != '原任务没有可复用的证据记录':
                raise
            evidence = {}
        write_json(folder / 'events.json', app.store.clean(events))
        write_json(folder / 'evidence.json', app.store.clean(evidence))
        rows = [row for item in evidence.get('evidence', []) for row in item.get('rows', [])]
        all_rows = [row for item in evidence.get('attempts', []) for row in item.get('rows', [])]
        measurement_rows = list(dict.fromkeys(rows + all_rows))
        issues = []
        if message['status'] in {'failed', 'interrupted', 'cancelled'}:
            issues.append('后端任务状态：' + message['status'])
        if not (message.get('content') or message.get('result_content')):
            issues.append('无可见回答或结果')
        expectation = case.get('expect')
        if expectation == 'rejected':
            metadata_tools = {'object_info', 'duration', 'select_object'}
            invalid_evidence = [e for e in evidence.get('evidence', [])
                                if case['id'] == '22' or e.get('tool') not in metadata_tools]
            if invalid_evidence: issues.append('无效对象/类型请求仍然返回了成功声学测量')
            if case.get('fixture') == 'derived' and any(a.get('execution') == 'delivered'
                   and 'To Pitch' in a.get('script', '') for a in evidence.get('attempts', [])):
                issues.append('类型不适用的 To Pitch 脚本已投递到 Praat，本应在执行前拒绝')
            rejection_text = message.get('content', '') + '\n' + '\n'.join(a.get('reason', '') for a in evidence.get('attempts', []))
            if not any(word in rejection_text for word in ('不存在','找不到','未找到','不支持','不能','无法','不适用','类型','没有','不可')):
                issues.append('没有明确说明对象不存在或类型不适用')
        elif expectation == 'discussion':
            if evidence.get('attempts'): issues.append('一般讨论意外调用了 Praat 工具')
        elif expectation == 'reuse':
            if not rows: issues.append('后续解释没有保留原测量证据')
            if not evidence.get('metrics', {}).get('l2_requests'): issues.append('未进入 L2 解释阶段')
            if any(e['type'] == 'activity' and e['payload'].get('activity', {}).get('type') == 'tool'
                   for e in events):
                issues.append('解释原结果时出现了新的工具执行活动')
        elif expectation == 'review':
            # Qualitative correctness requires inspection; do not invent a semantic oracle.
            status = 'fail' if issues else 'partial' if message['status'] == 'partial' else 'review'
            return dict(status=status, issues=issues + ['需要人工核对报告是否区分事实、建议和缺口'], rows=rows,
                        message=message, task=task, submitted_text=text)
        else:
            issues += checks(current_case, measurement_rows)
            missing = tool_coverage(current_case, evidence)
            # No successful evidence is correct when an acoustic value is undefined.
            undefined = any(w in '\n'.join(measurement_rows).lower() for w in UNAVAILABLE)
            if missing and not undefined:
                issues.append('缺少目标工具或等价测量参数：' + str(missing))
        status = measurement_status(current_case, measurement_rows, issues)
        if status == 'pass' and message['status'] == 'partial':
            status = 'partial'
            issues.append('实测检查通过，但后端报告尚未完成；请查看 message.content')
        return dict(status=status, issues=issues,
                    rows=measurement_rows, message=message, task=task, submitted_text=text,
                    bound_selection=chosen.selection)
    finally:
        # Never restore selection while a timed-out task can still be executing.
        if not any(t['thread'].is_alive() for t in app.tasks.values()):
            if temporary_sound:
                select(f'selectObject: {temporary_sound.id}\nRemove')
            select(restore)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('plan', 'tools', 'live'), default='plan')
    parser.add_argument('--audio', type=Path, default=PROJECT / 'test/fon/examples/sounds/aaaa02.wav')
    parser.add_argument('--praat', type=Path, default=PROJECT / 'Praat.exe')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--provider', choices=('configured', 'local', 'api'), default='configured',
                        help='只在诊断副本中选择本地或 API 路径')
    parser.add_argument('--context-tokens', type=int, help='本地诊断上下文窗口')
    parser.add_argument('--object', type=int)
    parser.add_argument('--cases', help='逗号分隔编号，例如 01,03,06,25,28')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--l2-deadline', type=int, help='只修改诊断配置副本中的 L2 秒数，便于对照排查')
    parser.add_argument('--thinking', choices=('auto','off','low','medium','high'), help='只修改诊断配置副本中的思考档位')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    args.audio, args.praat = args.audio.resolve(), args.praat.resolve()
    cases = json.loads(CASES_PATH.read_text(encoding='utf-8'))
    if args.cases:
        ids = {v.strip().zfill(2) for v in args.cases.split(',')}
        unknown = ids - {c['id'] for c in cases}
        if unknown: parser.error('未知编号：' + ','.join(sorted(unknown)))
        cases = [c for c in cases if c['id'] in ids]
    if args.mode == 'plan':
        for c in cases: print(f"{c['id']} [{c['group']}] {c['prompt']}")
        return 0
    root = (args.output or PROJECT / '_diag' / ('backend-' + time.strftime('%Y%m%d-%H%M%S'))).resolve()
    root.mkdir(parents=True, exist_ok=False)
    results, app, config_copy, sessions = [], None, None, {}
    try:
        if args.mode == 'live': app, config_copy = live_app(args, root)
        for case in cases:
            started = time.monotonic(); folder = root / case['id']; folder.mkdir()
            stop = False
            try:
                result = run_tools(case, args, folder) if app is None else run_live(case, args, folder, app, sessions)
            except Exception as error:
                message = str(error) if app is None else app.store.clean(str(error))
                result = dict(status='fail', issues=[type(error).__name__ + ': ' + message], rows=[])
                stop = bool(app)  # Unknown dispatch must not be followed by new requests.
            result = dict(id=case['id'], group=case['group'], prompt=result.get('submitted_text', case['prompt']),
                          seconds=round(time.monotonic() - started, 3), **result)
            if app: result = app.store.clean(result)
            write_json(folder / 'result.json', result); results.append(result)
            report(root, results, args.mode)
            print(f"{case['id']} {result['status']} {result['seconds']:.2f}s {case['group']}", flush=True)
            if stop: break
    finally:
        if app: app.close()
        if config_copy: config_copy.unlink(missing_ok=True)
    print(str(root / 'report.md'), flush=True)
    return int(any(r['status'] == 'fail' for r in results))


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    raise SystemExit(main())
