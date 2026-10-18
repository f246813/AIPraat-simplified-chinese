"""黄金集门禁：跑一遍固定的 10 条目标，按阈值判定这次改动能不能合。

设计见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 10 节（S6）。
底层复用 ``bench_guard_rate``（真模型 + 替身 Praat）；本脚本只负责**判定**：

- 交付正文不得出现内部术语（阶段代号、规则名、字段名）；
- 阻断次数、请求数、验证通过率达到阈值；
- 任何一轮抛异常都算失败。

用法：

    python ai/tests/golden_backend.py --model mock           # 离线骨架自检
    python ai/tests/golden_backend.py --model live --runs 10 # 真模型门禁（花 API）

退出码：0 通过，1 未达标（会打印逐条原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

AI_ROOT = Path(__file__).resolve().parents[1]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from praat_ai import delivery_text                                    # noqa: E402

import bench_guard_rate as bench                                      # noqa: E402

#: 默认阈值。基线（S1 前）是 verified 0/10、阻断 26、请求 8.1/轮、术语 10/10。
#: 阻断分两类：报告侧（守卫假阳性，必须为 0）与工具侧（参数真错，允许少量）。
DEFAULTS = dict(runs=10, min_verified=0.5, max_report_blocks=0, max_tool_blocks=2,
                max_requests=12.0)


def _blocks(rows) -> tuple[dict[str, int], dict[str, int]]:
    """按规则前缀把阻断拆成「报告侧」与「工具侧」。"""

    report: dict[str, int] = {}
    tool: dict[str, int] = {}
    for row in rows:
        for rule, count in (row.get('guard_blocks') or {}).items():
            target = tool if not str(rule).startswith('report.') else report
            target[rule] = target.get(rule, 0) + int(count)
    return report, tool


def evaluate(rows, *, min_verified=DEFAULTS['min_verified'],
             max_report_blocks=DEFAULTS['max_report_blocks'],
             max_tool_blocks=DEFAULTS['max_tool_blocks'],
             max_requests=DEFAULTS['max_requests']) -> list[str]:
    """返回未达标原因列表；空列表表示通过。"""

    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return ['没有任何一轮结果']
    failures: list[str] = []
    errors = [f"第 {row.get('index')} 轮异常：{row['error']}" for row in rows if row.get('error')]
    failures.extend(errors)
    dirty = [row.get('index') for row in rows
             if delivery_text.has_internal_terms(row.get('content') or '')]
    if dirty:
        failures.append(f'交付正文含内部术语：第 {dirty} 轮')
    report_blocks, tool_blocks = _blocks(rows)
    if sum(report_blocks.values()) > max_report_blocks:
        failures.append(f'报告侧阻断 {sum(report_blocks.values())} 次 > {max_report_blocks}：{report_blocks}')
    if sum(tool_blocks.values()) > max_tool_blocks:
        failures.append(f'工具侧阻断 {sum(tool_blocks.values())} 次 > {max_tool_blocks}：{tool_blocks}')
    verified = sum(1 for row in rows if row.get('report_verified'))
    rate = verified / len(rows)
    if rate < min_verified:
        failures.append(f'核对通过率 {verified}/{len(rows)} < {min_verified}')
    requests = sum(row.get('requests') or 0 for row in rows) / len(rows)
    if requests > max_requests:
        failures.append(f'平均请求 {requests:.1f}/轮 > {max_requests}')
    return failures


def report(rows) -> str:
    """一行行打印每轮结果，便于对比基线。"""

    lines = [f"{'#':>2} {'status':<9} {'verified':<9} {'req':>3} {'秒':>6} {'术语':<4} 阻断"]
    for row in rows:
        content = row.get('content') or ''
        lines.append(
            f"{row.get('index'):>2} {str(row.get('status')):<9} "
            f"{str(bool(row.get('report_verified'))):<9} {row.get('requests') or 0:>3} "
            f"{row.get('wall_seconds') or 0:>6} "
            f"{('有' if delivery_text.has_internal_terms(content) else '-'):<4} "
            f"{row.get('guard_blocks') or '-'}")
    return '\n'.join(lines)


def main(argv=None) -> int:
    defaults = DEFAULTS
    parser = argparse.ArgumentParser(description='黄金集门禁（默认走 mock，不花钱）')
    parser.add_argument('--config', default=str(Path.home() / 'AIPraat' / 'ai_config.json'),
                        help='ai_config.json 路径（live 模式必需）')
    parser.add_argument('--ai-root', default=str(AI_ROOT))
    parser.add_argument('--model', choices=('mock', 'live'), default='mock')
    parser.add_argument('--runs', type=int, default=defaults['runs'])
    parser.add_argument('--min-verified', type=float, default=defaults['min_verified'])
    parser.add_argument('--max-report-blocks', type=int, default=defaults['max_report_blocks'])
    parser.add_argument('--max-tool-blocks', type=int, default=defaults['max_tool_blocks'])
    parser.add_argument('--max-requests', type=float, default=defaults['max_requests'])
    parser.add_argument('--out', default=str(Path(__file__).resolve().parent / 'golden_backend.json'))
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding='utf-8')
    if args.model == 'mock' and not Path(args.config).is_file():
        # 离线自检不需要真实配置：造一份最小 api 配置，绝不联网。
        import tempfile
        generated = Path(tempfile.mkdtemp()) / 'ai_config.json'
        generated.write_text(json.dumps({
            'api': {'enabled': True, 'base_url': 'https://test.invalid/v1',
                    'model': 'mock', 'api_key': 'EMPTY'},
            'modern': {'workflow': {'preset': 'balanced', 'router_mode': 'off'}},
        }, ensure_ascii=False), encoding='utf-8')
        args.config = str(generated)

    bench.run(SimpleNamespace(config=args.config, ai_root=args.ai_root, runs=args.runs,
                              out=args.out, model=args.model))
    rows = json.loads(Path(args.out).read_text(encoding='utf-8'))
    print('\n' + report(rows))
    failures = evaluate(rows, min_verified=args.min_verified,
                        max_report_blocks=args.max_report_blocks,
                        max_tool_blocks=args.max_tool_blocks,
                        max_requests=args.max_requests)
    if failures:
        print('\n门禁未通过：')
        for item in failures:
            print('  - ' + item)
        return 1
    verified = sum(1 for row in rows if row.get('report_verified'))
    report_blocks, tool_blocks = _blocks(rows)
    print(f'\n门禁通过：{verified}/{len(rows)} 核对通过，'
          f'报告侧阻断 {sum(report_blocks.values())} 次，工具侧 {sum(tool_blocks.values())} 次')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
