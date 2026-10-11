"""Extra backend scenarios: 44 tool-layer cases + 22 live RPC cases.

Companion to ``run_backend_scenarios.py`` (GPT's 28-case suite). It deliberately
reuses that file's verified machinery (``run_tools``, ``live_app``, console
decoding, fixture sound) and adds declarative expectations for the checks the
28 cases never made: uncovered tools/parameters, argument contract boundaries,
scope honesty on tools without a range argument, multi-turn new-vs-reused
measurements, prompt injection, language and unit handling.

    python ai/tests/run_backend_scenarios_extra.py                  # list cases
    python ai/tests/run_backend_scenarios_extra.py --mode tools
    python ai/tests/run_backend_scenarios_extra.py --mode live --object 1
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

AI = Path(__file__).resolve().parents[1]
PROJECT = AI.parent
sys.path.insert(0, str(AI))

_spec = importlib.util.spec_from_file_location('run_backend_scenarios', Path(__file__).with_name('run_backend_scenarios.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

from praat_ai import tools  # noqa: E402

CASES_PATH = Path(__file__).with_name('backend_scenarios_extra.json')
UNAVAILABLE = base.UNAVAILABLE
REJECTION_WORDS = ('不存在', '找不到', '未找到', '不支持', '不能', '无法', '不适用', '类型', '没有', '不可', '无效')
HONEST_WORDS = ('无法', '不能', '算不出', '没有', '不足以', '未找到', '不可', '无效', '静音')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def text_of(*parts):
    return '\n'.join(str(p or '') for p in parts)


# --------------------------------------------------------------------------- tools


def finalize_tools(case, result):
    """Post-checks the base runner cannot express."""
    issues = list(result.get('issues', []))
    rows = '\n'.join(result.get('rows') or [])
    consoles = text_of(*[step.get('console') for step in result.get('steps') or []])
    expect = case.get('expect')
    if expect == 'runtime_error':
        want = case.get('error_contains', '')
        if want and want in consoles:
            return dict(result, status='pass', issues=[],
                        note='运行时拒绝，控制台包含预期说明：' + want)
        if row_has_number(rows):
            issues.append('运行时失败却仍写出了数值行，可能把无效范围当成有效测量')
        return dict(result, status='fail', issues=issues + [f'运行时错误里没有出现 {want!r}'])
    if expect == 'rejected':
        want = case.get('error_contains', '')
        rejection = str(result.get('rejection') or '')
        if want and want not in rejection:
            issues.append(f'拒绝理由里没有出现 {want!r}：{rejection}')
        return dict(result, status='fail' if issues else result.get('status', 'pass'), issues=issues)
    flag = case.get('flag_if_contains')
    if flag and flag in rows:
        issues.append('可疑输出：' + flag + ' —— 需要人工确认这是否是正确诊断')
        return dict(result, status='review', issues=issues)
    return dict(result, issues=issues)


def row_has_number(text):
    import re
    return bool(re.search(r'\d+\.\d+', text))


# --------------------------------------------------------------------------- live


def select_object(select, script):
    select(script)


def scope_matches(arguments, tool, span, tolerance=0.005):
    got = (arguments.get('from', arguments.get('start')), arguments.get('to', arguments.get('end')))
    if got == (None, None):
        return False
    try:
        return abs(float(got[0]) - span[0]) <= tolerance and abs(float(got[1]) - span[1]) <= tolerance
    except (TypeError, ValueError):
        return False


def run_live_case(case, args, folder, app, sessions, parents):
    from praat_ai import chat, delivery
    from praat_ai.modern_execution import _send, _gate, PRAAT_LOCK

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
            # 核对式闸门：投递状态不明时先让这一次去核对，而不是把封锁信息当成用例失败
            # （2026-10-07 实测：应用其实已经完成任务，是收尾选择被封锁信息挡住了）。
            _gate(threading.Event(), reconcile=True)
            ok, _, note, fact = _send(chat.praat_executable(), chat.context_pid(target),
                                      tools._assemble(script.splitlines(), tools.ToolContext(available, chat.result_path(), chat.state_path())),
                                      threading.Event())
            if not ok or fact != delivery.DELIVERED:
                raise RuntimeError(note or '选择操作投递未确认')

    derived = next((r for r in available if r.class_name == 'Harmonicity' and r.selected), None) or next(
        (r for r in available if r.class_name == 'Harmonicity'), None)
    text = case['prompt'].format(sound=chosen.id, derived=derived.id if derived else 0)
    sid = sessions.get(case.get('followup', ''))
    if not sid:
        if case.get('followup'):
            return dict(status='needs_fixture', issues=['请同时运行前置场景 ' + case['followup']], rows=[])
        sid = app.rpc('sessions.create', dict(title=f"扩展诊断 {case['id']} {case['group']}"))['id']
    sessions[case['id']] = sid
    parent = parents.get(case.get('followup', ''), {}) if case.get('followup') else {}
    temporary_sound = None
    try:
        if case.get('fixture') == 'silence':
            select('Create Sound from formula: "extra_diagnostic_silence", 1, 0, 2, 16000, "0"')
            fresh = tools.parse_object_context(app.executor.capture_target('查询当前对象'))
            temporary_sound = next(r for r in fresh if r.id not in {o.id for o in available}
                                   and r.class_name == 'Sound' and 'extra_diagnostic_silence' in r.name)
            chosen = temporary_sound
            text = case['prompt'].format(sound=chosen.id, derived=0)
        if not case.get('followup'):
            select(f'selectObject: {chosen.id}')
        events, task = [], None
        try:
            task = app.rpc('tasks.submit', dict(sessionId=sid, text=text))
        except Exception as error:
            rejection = app.store.clean(str(error))
            if case.get('require_rejection') or case.get('require_rejection_or_graceful'):
                return dict(status='pass', issues=[], rows=[], rejection=rejection,
                            submitted_text=text, delivery='not_submitted',
                            note='提交前被拒绝：' + rejection)
            if case.get('require_partial_rejection'):
                return dict(status='review', issues=[
                    '整个请求在提交前被拒绝，没有对有效对象给出任何测量：' + rejection],
                    rows=[], rejection=rejection, submitted_text=text, delivery='not_submitted')
            return dict(status='fail', issues=['提交即失败：' + rejection], rows=[],
                        rejection=rejection, submitted_text=text, delivery='not_submitted')
        deadline = time.monotonic() + args.timeout
        cursor = 0
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
        rows = list(dict.fromkeys(rows + [row for item in evidence.get('attempts', []) for row in item.get('rows', [])]))
        attempts = evidence.get('attempts', [])
        content = message.get('content') or message.get('result_content') or ''
        issues = []
        if message['status'] in {'failed', 'interrupted', 'cancelled'}:
            issues.append('后端任务状态：' + message['status'])
        if not content:
            issues.append('无可见回答或结果')
        new_success = [a for a in attempts if a.get('status') == 'success'
                       and a.get('execution', '') not in {'unknown', 'blocked', 'not_delivered'}
                       and (a.get('tool'), json.dumps(a.get('arguments'), sort_keys=True, ensure_ascii=False))
                       not in parent.get('signatures', set())]
        all_text = text_of(rows, content, *[a.get('reason', '') for a in attempts])

        if case.get('require_no_tools'):
            if attempts or rows:
                issues.append('本应只走对话，却执行了 Praat 工具：' + str([a.get('tool') for a in attempts]))
        if case.get('require_rejection'):
            if new_success:
                issues.append('无效对象/类型请求仍然返回了成功测量')
            if not any(word in all_text for word in REJECTION_WORDS):
                issues.append('没有明确说明对象不存在或类型不适用')
        if case.get('require_rejection_or_graceful'):
            graceful = any(word in all_text for word in REJECTION_WORDS) or '查询范围无效' in all_text
            if new_success and row_has_number('\n'.join(rows)):
                issues.append('范围反了却返回了有效测量数值')
            if not graceful:
                issues.append('范围无效时没有给出明确说明')
        if case.get('require_honest_unavailable'):
            if not any(word in all_text for word in HONEST_WORDS):
                issues.append('静音对象上没有说明无法计算')
            if new_success and row_has_number('\n'.join(rows)) and not any(word in all_text for word in HONEST_WORDS):
                issues.append('静音对象上给出了数值却没有说明')
        if case.get('require_partial_rejection'):
            if not any(word in all_text for word in REJECTION_WORDS):
                issues.append('没有说明第二个对象找不到')
            if not any('时长' in row for row in rows):
                issues.append('有效对象的时长没有测出来')
        if case.get('require_new_tools') and not new_success:
            issues.append('后续问题没有产生新的测量，可能复用了不相关的旧结果')
        if case.get('require_no_new_tools'):
            if attempts:
                issues.append('只需解释已有结果，却重新执行了工具：' + str([a.get('tool') for a in attempts]))
            if not rows:
                issues.append('解释时没有保留原测量证据')
        span = case.get('require_scope')
        if span:
            matched = [a for a in attempts if a.get('status') == 'success' and a.get('tool') == span['tool']]
            if not matched:
                issues.append('没有出现 ' + span['tool'] + ' 的成功测量')
            elif not any(scope_matches(a.get('arguments') or {}, span['tool'], span['range']) for a in matched):
                issues.append(f"测量范围不是要求的 {span['range']}，实际 " + str([a.get('arguments') for a in matched]))
        disclosure = case.get('require_turn_scope_disclosure') or case.get('require_scope_disclosure')
        if disclosure:
            low, high = disclosure['range']
            turn_rows = text_of(*rows)
            in_turn = f'{low:.3f}' in turn_rows and f'{high:.3f}' in turn_rows
            for tool_name in disclosure['tools']:
                for a in attempts:
                    if a.get('tool') == tool_name and a.get('status') == 'success':
                        body = text_of(*a.get('rows', []))
                        scoped = scope_matches(a.get('arguments') or {}, tool_name, disclosure['range'])
                        if not scoped and not in_turn and (
                                f'{low:.3f}' not in body or f'{high:.3f}' not in body):
                            issues.append(f'{tool_name} 被要求只算 {low}–{high} 秒，结果行没有交代范围，'
                                          f'可能是整段数值：{body[:160]}')
        for word in case.get('forbid_content', []):
            if word in content:
                issues.append(f'回答里出现了不应出现的内容：{word}')
        for word in case.get('content_markers', []):
            if word.lower() not in content.lower():
                issues.append('回答里缺少 ' + word)
        any_words = case.get('content_any')
        if any_words and not any(word.lower() in content.lower() for word in any_words):
            issues.append('回答里没有出现任何一项应有的说明：' + '/'.join(any_words))
        for word in case.get('forbid_evidence_numbers', []):
            if word in '\n'.join(rows):
                issues.append('测量行里出现了用户指定的伪造数值：' + word)
        if case.get('steps'):
            issues += base.checks(case, rows)
            missing = base.tool_coverage(case, evidence)
            undefined = any(w in '\n'.join(rows).lower() for w in UNAVAILABLE)
            if missing and not undefined and not case.get('require_rejection'):
                issues.append('缺少目标工具或等价测量参数：' + str(missing))
        status = base.measurement_status(case, rows, issues)
        if status == 'pass' and message['status'] == 'partial':
            status = 'partial'
            issues.append('实测检查通过，但后端报告尚未完成')
        result = dict(status=status, issues=issues, rows=rows, content=content,
                      message=message, task=task, submitted_text=text,
                      attempts_summary=[dict(tool=a.get('tool'), arguments=a.get('arguments'),
                                             status=a.get('status'), execution=a.get('execution', ''),
                                             reason=a.get('reason', '')) for a in attempts])
        parents[case['id']] = dict(
            signatures=set(parent.get('signatures', set())) | {
                (a.get('tool'), json.dumps(a.get('arguments'), sort_keys=True, ensure_ascii=False))
                for a in new_success},
            evidence_rows=rows)
        return result
    finally:
        if not any(t['thread'].is_alive() for t in app.tasks.values()):
            if temporary_sound:
                select(f'selectObject: {temporary_sound.id}\nRemove')
            select(restore)


# --------------------------------------------------------------------------- main


def report(root, results, mode, cases):
    counts = {}
    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    write_json(root / 'summary.json', dict(mode=mode, counts=counts, cases=results))
    title = ('真实 Praat 工具检查（未测试模型选工具、原生投递和前端渲染）' if mode == 'tools'
             else '真实桌面 RPC 后端检查（未测试 React/WebView 渲染）')
    lines = ['# 扩展场景后端排查（GPT 28 项之外）', '', title, '',
             f'统计：{counts}', '',
             f'本批共 {len(cases)} 项，均为 28 项固定排查未覆盖的问题。', '',
             '| 编号 | 场景 | 结果 | 耗时（秒） |', '|---|---|---|---|']
    lines += [f"| {r['id']} | {r['group']} | {r['status']} | {r['seconds']:.2f} |" for r in results]
    for r in results:
        lines += ['', f"## {r['id']} {r['group']}", '', r['prompt'], '',
                  f"结果：{r['status']}", '']
        lines += ['- ' + issue for issue in r.get('issues', [])]
        if r.get('rows'):
            lines += ['', '```text', '\n'.join(r['rows'][:30]), '```']
    (root / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('plan', 'tools', 'live'), default='plan')
    parser.add_argument('--audio', type=Path, default=PROJECT / 'test/fon/examples/sounds/aaaa02.wav')
    parser.add_argument('--praat', type=Path, default=PROJECT / 'Praat.exe')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--provider', choices=('configured', 'local', 'api'), default='configured')
    parser.add_argument('--context-tokens', type=int)
    parser.add_argument('--object', type=int)
    parser.add_argument('--cases')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--l2-deadline', type=int)
    parser.add_argument('--thinking', choices=('auto', 'off', 'low', 'medium', 'high'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--continue-after-unknown', action='store_true',
                        help='投递状态不明时继续跑后面的用例（派发器现在会由一次核对 ping 自愈）')
    args = parser.parse_args()
    args.audio, args.praat = args.audio.resolve(), args.praat.resolve()
    cases = json.loads(CASES_PATH.read_text(encoding='utf-8'))
    if args.cases:
        ids = {v.strip().upper() for v in args.cases.split(',')}
        unknown = ids - {c['id'] for c in cases}
        if unknown:
            parser.error('未知编号：' + ','.join(sorted(unknown)))
        cases = [c for c in cases if c['id'] in ids]
    if args.mode != 'plan':
        cases = [c for c in cases if c.get('mode') == args.mode]
    if args.mode == 'plan':
        for c in cases:
            print(f"{c['id']} [{c['mode']}] [{c['group']}] {c['prompt']}")
        return 0
    root = (args.output or PROJECT / '_diag' / ('backend-extra-' + args.mode + '-' + time.strftime('%Y%m%d-%H%M%S'))).resolve()
    root.mkdir(parents=True, exist_ok=False)
    results, app, config_copy, sessions, parents = [], None, None, {}, {}
    try:
        if args.mode == 'live':
            app, config_copy = base.live_app(args, root)
        for case in cases:
            started = time.monotonic()
            folder = root / case['id']
            folder.mkdir()
            stop = False
            try:
                if args.mode == 'tools':
                    result = finalize_tools(case, base.run_tools(case, args, folder))
                else:
                    result = run_live_case(case, args, folder, app, sessions, parents)
            except Exception as error:
                message = str(error) if app is None else app.store.clean(str(error))
                result = dict(status='fail', issues=[type(error).__name__ + ': ' + message], rows=[])
                # 默认沿用「投递状态不明就停」：不确定的派发不该紧跟新请求。2026-10-07 起
                # 派发器会被一次成功的核对 ping 解除封锁，所以要接着跑时可以显式打开。
                stop = bool(app) and not args.continue_after_unknown
            result = dict(id=case['id'], group=case['group'], prompt=result.get('submitted_text', case['prompt']),
                          seconds=round(time.monotonic() - started, 3), **result)
            if app:
                result = app.store.clean(result)
            write_json(folder / 'result.json', result)
            results.append(result)
            report(root, results, args.mode, cases)
            print(f"{case['id']} {result['status']} {result['seconds']:.2f}s {case['group']}", flush=True)
            if stop:
                break
    finally:
        if app:
            app.close()
        if config_copy:
            config_copy.unlink(missing_ok=True)
    report(root, results, args.mode, cases)
    print(str(root / 'report.md'), flush=True)
    return 0


if __name__ == '__main__':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    raise SystemExit(main())
