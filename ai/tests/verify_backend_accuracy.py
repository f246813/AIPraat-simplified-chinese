"""Real API and native Praat accuracy probes; --send is required to spend requests."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import numpy as np
import parselmouth

import verify_ai_dialogue_acceptance as acceptance
from praat_ai import answer_contract, chat, cloud_agent, execution_core, report_guards, tools
from praat_ai.config import load_config, api_is_active


CASES = {
    'slope_up': ('测量对象 1 在 0.2 到 0.8 秒的基频平均绝对斜率（pitch_slope，Hz/s），只报告这个量，不判断声调类别。', 60),
    'slope_down': ('测量对象 1 在 0.2 到 0.8 秒的基频平均绝对斜率（pitch_slope，Hz/s），只报告这个量，不判断声调类别。', -60),
    'slope_level': ('测量对象 1 在 0.2 到 0.8 秒的基频平均绝对斜率（pitch_slope，Hz/s），只报告这个量，不判断声调类别。', 0),
    'silent': ('测量对象 1 在 0.2 到 0.8 秒的平均基频和基频平均绝对斜率。没有有效基频时请明确说明。', None),
    'vot_positive': ('对象 1 的人工标注：爆破是0.3秒，浊音起始是0.33秒。测量VOT。', .33),
    'vot_negative': ('对象 1 的人工标注：爆破是0.3秒，浊音起始是0.2秒，清晰元音起始是0.33秒。测量VOT，并说明它和爆破至清晰元音间隔的区别。', .2),
    'vot_zero': ('对象 1 的人工标注：爆破是0.3秒，浊音起始是0.3秒。测量VOT。', .3),
    'pronunciation': ('一般知识：日语 /ɯ/ 和 /u/ 在唇形和舌位上有什么区别？给出纠音练习，不测量当前音频。', 0),
    'pitch': ('测量对象 1 在 0.2 到 0.8 秒的平均基频。', 0),
    'difference': ('分别测量对象 1 和对象 2 的平均基频，然后告诉我两者的差值。', 0),
}


def fixture(rate):
    times = np.arange(16000) / 16000
    values = np.zeros_like(times) if rate is None else .3*np.sin(2*np.pi*(220*times+rate/2*times**2))
    return [parselmouth.Sound(values, sampling_frequency=16000),
            parselmouth.Sound(.3*np.sin(2*np.pi*200*times), sampling_frequency=16000)]


SOURCE = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tfixture\t1\t\t\n2\tSound\treference\t0\t\t\n'


def replay(paths):
    """Re-execute recorded tool proposals and audit saved model drafts, with no API."""
    records = []
    for path in paths:
        for saved in json.loads(path.read_text(encoding='utf-8')):
            name = saved['case']
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                executor = acceptance.PraatExecutor(root, fixture(CASES[name][1]))
                context = tools.ToolContext(tools.parse_object_context(SOURCE), root/'chat_result.tsv', root/'chat_state.txt')
                state = acceptance.build_state(saved['goal'], SOURCE, load_config())
                for attempt in saved['attempts']:
                    step = chat._execute_action(dict(tool=attempt['tool'], arguments=attempt['arguments']), context, executor, 1)
                    valid = execution_core.valid_evidence(step, mutating=tools.is_mutating(step.tool),
                        rows_valid=cloud_agent.valid_measurement_rows(step.tool, step.results))
                    if valid:
                        state.evidence.append(execution_core.evidence_row(step, SOURCE))
                draft = (saved.get('drafts') or [{}])[0].get('analysis', saved['report'])
                if name == 'pronunciation':
                    issues = answer_contract.knowledge_issues(saved['goal'], draft)
                    body = answer_contract.knowledge_fallback(saved['goal']) if issues else draft
                    status = 'reviewed_reference' if issues else 'unchanged'
                else:
                    body, status = report_guards.guard_delivered_text(state, draft)
                records.append(dict(case=name, replay_guard_status=status, report=body,
                    measures=[m for e in state.evidence for m in e.acoustic.get('measures', [])],
                    native_executions=len(executor.executions)))
    return records


def run_case(name, ledger):
    goal, rate = CASES[name]
    config = load_config()
    if not api_is_active(config):
        raise RuntimeError('API mode must already be enabled')
    sounds = fixture(rate)
    source = SOURCE
    state = acceptance.build_state(goal, source, config)
    drafts = []
    validate = report_guards.validate_report

    def capture(state, report, **kwargs):
        drafts.append(dict(analysis=report.analysis, coverage=[asdict(x) if not hasattr(x, 'model_dump') else x.model_dump() for x in report.coverage]))
        return validate(state, report, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        context = tools.ToolContext(tools.parse_object_context(source), root/'chat_result.tsv', root/'chat_state.txt')
        executor = acceptance.PraatExecutor(root, sounds)
        attempts = []
        action = acceptance.make_execute_action(executor, context, attempts)
        counters = acceptance.new_counters(name)
        with acceptance.install_send_guard(ledger, counters, name), patch.object(report_guards, 'validate_report', capture):
            if name == 'pronunciation':
                cloud_agent.run_dialogue_turn(config, state, kind='concept', cancel=threading.Event(), on_text=lambda _: None)
            else:
                cloud_agent.run_cloud_turn(config, state, execute_action=action, cancel=threading.Event())
        record = dict(case=name, goal=goal, status=state.status, report=state.report, reason=state.reason,
                      requests=counters['sends'], attempts=attempts, evidence=[asdict(e) for e in state.evidence],
                      metrics=state.metrics, drafts=drafts, manual_report_review_required=True)
        # Synthetic fixtures establish acoustic truth, never phoneme identity.
        measures = [m for e in state.evidence for m in e.acoustic.get('measures', [])]
        if name.startswith('slope_'):
            record['accuracy_passed'] = any(m['name']=='pitch.slope_absolute' and m['value'] is not None and abs(m['value']-abs(rate))<1 for m in measures)
        elif name.startswith('vot_'):
            record['accuracy_passed'] = any(m['name']=='vot_ms' and m['value'] is not None and abs(m['value']-(rate-.3)*1000)<1e-6 for m in measures)
        elif name == 'silent':
            record['accuracy_passed'] = bool(measures) and all(m['value'] is None for m in measures)
        else:
            record['accuracy_passed'] = None
        secrets = [config.api.api_key, config.api.base_url]
        return json.loads(acceptance._scrub_text(json.dumps(record, ensure_ascii=False), root, secrets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--case', choices=CASES, action='append', default=[])
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--replay', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.replay:
        if args.send:
            parser.error('--replay and --send cannot be combined')
        args.output.write_text(json.dumps(replay(args.replay), ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    if not args.send:
        print(json.dumps({c: CASES[c][0] for c in args.case}, ensure_ascii=False))
        return 0
    if not args.case or args.ledger is None:
        parser.error('--send requires --case and --ledger')
    ledger = acceptance.SendLedger(args.ledger, 50)
    records = []
    for name in args.case:
        if ledger.remaining <= 0:
            break
        record = run_case(name, ledger)
        records.append(record)
        args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(name, record['status'], 'accuracy=', record['accuracy_passed'], 'requests=',record['requests'], 'ledger=',ledger.attempts_total, flush=True)
    return int(len(records) != len(args.case) or any(r['accuracy_passed'] is False for r in records))


if __name__ == '__main__':
    raise SystemExit(main())
