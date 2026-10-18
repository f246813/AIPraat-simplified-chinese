"""守卫命中率基准：真实云端模型 + 替身 Praat 派发，跑完整 L0→L1→L2 管线。

设计依据见 ai/docs/backend-workflow-redesign.zh-CN.md（S0 黄金集的门禁原型）。
它沿用 ai/tests/test_modern_execution.py 的手法：把 ``chat._execute_action``
换成返回固定测量行的替身，其余（工具守卫、云端 agent、报告守卫、预算、收尾）
全部走真实实现，所以出来的是路径行为，不是测量数值。

用法：

    python ai/tests/bench_guard_rate.py --config <ai_config.json> [--runs 10]
                                       [--ai-root <仓库 ai 目录>] [--out <json 路径>]
                                       [--model mock]

``--model mock`` 用固定脚本模型（不联网、不花钱），只用于验证骨架本身。
默认用配置里的真实 API。

输出：每轮状态/请求数/墙钟/guard_blocks/guard_repairs/block_log，落一个 JSON，
并打印按规则聚合的命中表。
"""
from __future__ import annotations

import argparse
import collections
import copy
import io
import json
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from unittest.mock import patch

PID = 13764
IDENTITY = {'executable': 'Praat.exe', 'started': 'win:134367314016208869'}
CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\tSound あなた\t1\t\t\n'
           f'# praat-pid={PID}\n# praat-protocol=2\n'
           '# modern-process=' + json.dumps(IDENTITY, ensure_ascii=False) + '\n')

MEASURE_ROWS = [
    ('mean_pitch', '平均基频', 94.16, 'Hz'), ('minimum_pitch', '最低基频', 77.01, 'Hz'),
    ('maximum_pitch', '最高基频', 119.24, 'Hz'), ('median_pitch', '基频中位数', 95.01, 'Hz'),
    ('sd_pitch', '基频标准差', 140.16, 'Hz'), ('mean_intensity', '平均强度', 50.70, 'dB'),
    ('maximum_intensity', '最高强度', 57.78, 'dB'), ('median_intensity', '强度中位数', 37.78, 'dB'),
    ('sd_intensity', '强度标准差', 10.00, 'dB'), ('hnr', '谐噪比 HNR', 10.14, 'dB'),
    ('rms', '有效值 RMS', 0.0067, 'Pa'), ('f1', '第 1 共振峰平均', 891.5, 'Hz'),
    ('f2', '第 2 共振峰平均', 1906.4, 'Hz'), ('f3', '第 3 共振峰平均', 3135.6, 'Hz'),
    ('b1', '第 1 共振峰带宽中位数', 621.3, 'Hz'), ('b2', '第 2 共振峰带宽中位数', 614.4, 'Hz'),
    ('b3', '第 3 共振峰带宽中位数', 608.6, 'Hz'),
]

#: 黄金目标集：覆盖概念题、单值快路径、解释、分段、共振峰、VOT、节奏、音调、练习建议。
GOALS = [
    '这是日语语音あなた，与标准音对比，指出发音不足之处与待改进点',
    '分析当前声音的音调走向，判断是不是平板型重音，并说明依据',
    '当前声音的平均基频和音高范围说明了什么？请解释这些测量结果',
    '比较这段录音与标准日语发音的元音共振峰差异，指出元音是否偏后',
    '分段测一下强度变化，解释后半段是不是太弱、该怎么练',
    '这段录音的 VOT 是多少？判断塞音是否送气不足',
    '整体评估这段朗读的节奏与停顿，指出需要改进的地方',
    '当前声音的谐噪比和 CPPS 说明什么？请解释这些数值的含义',
    '帮我分析录音中 a 元音的 F1 和 F2，并给出可执行的练习建议',
    '与东京标准音对比，这段あなた的音高重音是否正确？给出改进点',
]

_TABLE = None


def measure_entry(param):
    """用 measures.tsv 的规范表解析参数名，生成与真机同格式的结果行。"""
    global _TABLE
    if _TABLE is None:
        from praat_ai import measures
        _TABLE = measures.load_table()
    name = _TABLE.canonical(param) or param
    entry = _TABLE.find(name)
    if entry is None:
        return None
    source = (entry.source or '').lower()
    if entry.unit == 'Hz':
        value = {'pitch': 95.0, 'formant': 1500.0}.get(source, 300.0)
    elif entry.unit == 'dB':
        value = 10.14 if 'harmonic' in source else 50.70
    elif entry.unit == 'Pa':
        value = 0.0067
    elif entry.unit in {'', '%'}:
        value = 42.0
    else:
        value = 12.5
    return entry.label, value, int(entry.decimals or 2), entry.unit


def _fmt(value):
    return f'{value:.2f}' if abs(value) >= 1 else f'{value:.4f}'


def _span(args, default=(0.0, 0.892)):
    start = args.get('from', args.get('start', default[0]))
    end = args.get('to', args.get('end', default[1]))
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        start, end = default
    return f'{start:.3f}', f'{end:.3f}'


def rows_for(name, args):
    """替身 Praat 的测量行：与真机输出同模板、同精度。"""
    a, b = _span(args)
    mid = (float(a) + float(b)) / 2
    if name == 'object_info':
        return True, ['Sound「Sound あなた」（id 1）：时长 0.892 秒，通道数 1，采样率 44100 Hz']
    if name == 'duration':
        return True, ['Sound あなた 总时长 = 0.892000 秒']
    if name == 'pitch_statistics':
        mean = 94.159 if (a, b) == ('0.000', '0.892') else round(80 + 16 * (1 - float(a) / 0.9), 3)
        return True, [f'基频统计（{a}–{b} 秒）：平均 {mean:.3f} Hz，最低 77.014 Hz，'
                      f'最高 119.243 Hz，最高点出现在 {mid:.3f} 秒']
    if name == 'intensity_statistics':
        mean = 50.700 if (a, b) == ('0.000', '0.892') else round(38 + 15 * (1 - float(a) / 0.9), 3)
        return True, [f'强度统计（{a}–{b} 秒）：平均 {mean:.3f} dB，最高 57.782 dB']
    if name in {'pitch', 'intensity', 'formant_frequency', 'formant_bandwidth'}:
        return True, [f'共振峰平均（{a}–{b} 秒）：第 1 共振峰 891.5 Hz，第 2 共振峰 1906.4 Hz']
    if name == 'formant_statistics':
        return True, [f'第 1 共振峰平均（{a}–{b} 秒）= 891.5 Hz',
                      f'第 2 共振峰平均（{a}–{b} 秒）= 1906.4 Hz']
    if name == 'harmonicity_statistics':
        return True, [f'谐噪比 HNR（{a}–{b} 秒）：平均 10.14 dB']
    if name == 'spectral_emphasis':
        return True, ['倒谱峰突出 CPPS = 13.56 dB']
    if name == 'intensity_slope':
        return True, [f'强度斜率（{a}–{b} 秒）= -1.20 dB/s']
    if name == 'hammarberg_index':
        return True, ['Hammarberg 指数 = -8.4 dB']
    if name == 'hl_ratio':
        return True, ['H1-H2 = 2.30 dB']
    if name == 'peak_to_average_ratio':
        return True, ['峰均比 = 12.40 dB']
    if name == 'vot':
        return True, ['VOT 估计值 = 0.0421 秒（42.1 毫秒）']
    if name == 'measure':
        wanted = [p.strip() for p in str(args.get('parameter', '')).split(',') if p.strip()]
        if not wanted:
            for key in ('parameters', 'params', 'param'):
                if args.get(key):
                    wanted = [p for p in re.split(r'[,\s]+', str(args[key])) if p]
                    break
        wanted = wanted or [item[0] for item in MEASURE_ROWS]
        out = []
        for param in wanted:
            row = next((item for item in MEASURE_ROWS if item[0] == param), None)
            if row is not None:
                out.append(f'{row[1]}（{a}–{b} 秒）= {_fmt(row[2])} {row[3]}')
                continue
            entry = measure_entry(param)
            if entry is not None:
                out.append(f'{entry[0]}（{a}–{b} 秒）= {entry[1]:.{entry[2]}f} {entry[3]}')
        return True, out
    if name == 'textgrid_info':
        return False, []
    return True, [f'{name}：已执行（替身派发）']


def _mock_factory(config):
    """离线骨架自检用的固定模型：只发一个 object_info 调用，然后给一段结论。"""
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):
        last = messages[-1]
        if getattr(last, 'parts', None) and any(
                getattr(part, 'tool_name', None) for part in last.parts):
            return '时长 0.892 秒，已记录。'
        return json.dumps({'analysis': '观测：时长 0.892 秒。一般知识：这是替身模型输出。',
                           'coverage': []}, ensure_ascii=False)
    return FunctionModel(respond)


def run(args):
    ai_root = Path(args.ai_root).resolve()
    sys.path.insert(0, str(ai_root))
    from praat_ai import chat, delivery, modern_execution as m, report_guards
    from praat_ai import modern_store, telemetry as telemetry_mod
    from praat_ai.config import load_config
    from praat_ai.workflow_policy import WorkflowPolicy

    config_path = Path(args.config).resolve()
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    base = load_config(config_path)
    base.workflow = WorkflowPolicy.validate(raw.get('modern', {}).get('workflow'))
    runtime_directory = Path(args.out).resolve().parent / 'bench-runtime'
    runtime_directory.mkdir(parents=True, exist_ok=True)
    # 每轮真实 metrics 折成遥测行写进 telemetry 表（与 app 用同一份 modern_store 口径），
    # 门禁结果因此可以事后聚合，而不只是看一眼终端输出。
    telemetry_store, telemetry_session = None, ''

    block_log: list[tuple[str, str]] = []
    real_block = report_guards._block

    def recording_block(state, rule, message):
        block_log.append((rule, message))
        return real_block(state, rule, message)

    def fake_execute_action(action, context, execute, index, environment=None):
        name = str(action.get('tool', ''))
        call_args = dict(action.get('arguments') or {})
        ok, rows = rows_for(name, call_args)
        observation = (f'工具 {name} 执行成功，结果：' + '；'.join(rows)) if ok else f'工具 {name} 没有可用结果'
        return chat.AgentStep(index, name, call_args, ok, observation,
                              script='selectObject: 1\n# stand-in', results=list(rows),
                              execution=delivery.DELIVERED)

    def send_script(*a, outcome=None, **k):
        if outcome is not None:
            outcome.append(delivery.DELIVERED)
        return True, ''

    results = []
    started = time.monotonic()
    with patch.object(m, '_POISON', ''), \
         patch.object(m, 'process_identity', return_value=IDENTITY), \
         patch.object(m, 'identities_match', return_value=True), \
         patch.object(chat, 'praat_executable', return_value='Praat.exe'), \
         patch.object(chat, 'praat_process_ids', return_value=[PID]), \
         patch.object(chat.sendpraat, 'send_mode', return_value='mock-native'), \
         patch.object(chat, 'object_context', side_effect=lambda: CONTEXT), \
         patch.object(chat, 'context_ping_script', return_value='PING'), \
         patch.object(chat, 'result_path', return_value=runtime_directory / 'result.tsv'), \
         patch.object(chat, 'state_path', return_value=runtime_directory / 'state.txt'), \
         patch.object(chat, '_send_script', side_effect=send_script), \
         patch.object(chat, '_read_failure', return_value=''), \
         patch.object(chat, '_read_results', return_value=[]), \
         patch.object(chat, '_execute_action', side_effect=fake_execute_action), \
         patch.object(report_guards, '_block', side_effect=recording_block):
        executor = m.ModernExecutor(runtime_directory, config_path)
        if args.model == 'mock':
            patcher = patch.object(m, 'CloudRuntime', side_effect=lambda: _runtime(_mock_factory))
            patcher.start()
        try:
            for index, goal in enumerate(GOALS[:args.runs], 1):
                block_log.clear()
                print(f'[{index}/{args.runs}] {goal}', flush=True)
                record = {'index': index, 'goal': goal}
                begin = time.monotonic()
                try:
                    result = executor.run(config=copy.deepcopy(base), text=goal, history=[],
                                          target=CONTEXT, cancel=threading.Event(),
                                          emit=lambda kind, payload: None, attachments=[],
                                          session_id='', task_id=f'bench-{index}',
                                          context_epoch=0, task_metrics={'task_id': f'bench-{index}'})
                    metrics = result.get('metrics') or {}
                    record.update(status=result.get('status'), requests=metrics.get('requests'),
                                  wall_seconds=round(time.monotonic() - begin, 1),
                                  report_verified=metrics.get('report_verified'),
                                  guard_blocks=metrics.get('guard_blocks') or {},
                                  guard_repairs=metrics.get('guard_repairs') or {},
                                  rejected_values=metrics.get('rejected_values') or [],
                                  audit=metrics.get('audit') or {},
                                  router=metrics.get('router') or {},
                                  finish_state=metrics.get('finish_state'),
                                  derived_accepted=metrics.get('report_derived_accepted', 0),
                                  general_assumed=metrics.get('report_general_value_assumed', 0),
                                  l2_seconds=metrics.get('l2_seconds'),
                                  content=result.get('content') or '',
                                  block_log=list(block_log))
                    if telemetry_store is None:
                        telemetry_store = modern_store.ModernStore(runtime_directory / 'telemetry.db')
                        telemetry_session = telemetry_store.new_session('黄金集门禁')['id']
                    telemetry_store.save_telemetry(
                        telemetry_session, f'bench-{index}',
                        telemetry_mod.payload(metrics, status=result.get('status') or ''))
                except Exception as error:                      # noqa: BLE001
                    record.update(error=f'{type(error).__name__}: {error}',
                                  traceback=traceback.format_exc()[-1500:],
                                  wall_seconds=round(time.monotonic() - begin, 1))
                results.append(record)
                print('    ->', json.dumps({k: record.get(k) for k in
                      ('status', 'requests', 'report_verified', 'audit', 'router', 'finish_state', 'error',
                       'guard_repairs', 'error', 'wall_seconds')}, ensure_ascii=False), flush=True)
                Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                          encoding='utf-8')
        finally:
            executor.close()
            if args.model == 'mock':
                patcher.stop()

    report = io.StringIO()
    blocks, block_runs = collections.Counter(), collections.Counter()
    repairs, repair_runs = collections.Counter(), collections.Counter()
    for record in results:
        for key, value in (record.get('guard_blocks') or {}).items():
            blocks[key] += value
            block_runs[key] += 1
        for key, value in (record.get('guard_repairs') or {}).items():
            repairs[key] += value
            repair_runs[key] += 1
    report.write(f'\n轮数 {len(results)}｜请求总数 {sum(r.get("requests") or 0 for r in results)}'
                 f'｜通过核对 {sum(1 for r in results if r.get("report_verified"))}/{len(results)}'
                 f'｜总墙钟 {round(time.monotonic() - started, 1)} s\n')
    report.write('\n阻断型：\n')
    for key, value in blocks.most_common():
        report.write(f'  {key:<32} {value:>3} 次　出现在 {block_runs[key]}/{len(results)} 轮\n')
    report.write('修正型：\n')
    for key, value in repairs.most_common():
        report.write(f'  {key:<32} {value:>3} 次　出现在 {repair_runs[key]}/{len(results)} 轮\n')
    print(report.getvalue())
    if telemetry_store is not None:
        summary = telemetry_store.telemetry_summary()
        print('遥测摘要：' + json.dumps(summary, ensure_ascii=False, indent=1))


def _runtime(factory):
    from praat_ai.cloud_runtime import CloudRuntime
    return CloudRuntime(model_factory=factory)


def main(argv=None):
    parser = argparse.ArgumentParser(description='守卫命中率基准（真模型 + 替身 Praat）')
    parser.add_argument('--config', required=True, help='ai_config.json 路径')
    parser.add_argument('--ai-root', default=str(Path(__file__).resolve().parents[1]),
                        help='仓库 ai 目录（含 praat_ai 包）')
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--out', default=str(Path(__file__).resolve().parent / 'bench_guard_rate.json'))
    parser.add_argument('--model', choices=('live', 'mock'), default='live')
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')
    run(args)


if __name__ == '__main__':
    main()
