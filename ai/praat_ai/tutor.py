from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any

from .analysis_progress import run_with_stop
from .bridge import PraatBridge, object_payload
from .config import AppConfig, load_config
from .models import AnalysisRequest, AnalysisResult, PhoneSpec
from .pronunciation import analyze_pronunciation, conclusion_caveat, explanation_evidence
from .pronunciation_rules import resolve_profile
from .qwen import QwenClient, QwenError
from .report import write_json, write_overlay_png, write_textgrid
from . import report_guards
from .server import QwenServerError, QwenServerManager
from .forced_alignment import AlignmentCancelled
from .ui import show_tutor_form
from .vram import detect_gpu, select_runtime_profile
from .mfa_task import task_alignment, explanation_payload
from .paths import installation_root


@dataclass(slots=True)
class TutorOutputs:
    json_path: Path
    textgrid_path: Path
    overlay_path: Path | None
    result: AnalysisResult
    request_payload: dict[str, Any] | None = None



def _explanation_context(request_payload: dict[str, Any] | None) -> tuple[str, Any, Any]:
    """从请求负载里取「这次分析针对哪些对象」，供解释守卫认对象与范围。"""

    payload = request_payload or {}
    reference = payload.get("resolved_reference") or {}
    learner = payload.get("resolved_learner") or {}
    rows = ["id\tclass\tname\tselected\tsel_start\tsel_end"]
    for item in (reference, learner):
        if not item.get("id"):
            continue
        rows.append(f"{item['id']}\t{item.get('class') or 'Sound'}\t{item.get('name') or ''}\t1\t\t")
    return "\n".join(rows) + "\n", reference.get("id"), learner.get("id")


def guard_explanation(
    result: AnalysisResult,
    text: str,
    *,
    request_payload: dict[str, Any] | None = None,
    caveat: bool = True,
) -> tuple[str, str]:
    """按本次测量核对可选模型解释，返回正文与 explanation_status。无法核对的数字遮蔽并标记，对比不完整时保留对应结论等级，视觉失败保留原因。"""

    context_text, reference_object, learner_object = _explanation_context(request_payload)
    state = explanation_evidence(
        result,
        context_text=context_text,
        reference_object=reference_object,
        learner_object=learner_object,
    )
    guarded, status = report_guards.guard_delivered_text(state, str(text or ""))
    provisional = any(measure.status == 'provisional' for phone in [*result.reference_phones, *result.learner_phones]
                      if (measure := phone.measurements.get('vot')) is not None)
    if provisional:
        sentences = re.split(r'(?<=[。！？\n])', guarded)
        for index, sentence in enumerate(sentences):
            if (re.search(r'VOT|起浊|预浊|释放地标', sentence, re.I)
                    and re.search(r'已确认|已验证|确认了|证实|证明|发音错误', sentence)
                    and not re.search(r'未|不|无法|不能|尚|待', sentence)):
                sentences[index] = 'VOT 地标仍为自动候选，不能据此确认地标归属或发音错误。'
                status = 'rejected_masked'
        guarded = ''.join(sentences)
    note = conclusion_caveat(result) if caveat else ""
    if note and note not in str(result.explanation or "") and note not in guarded:
        guarded = (guarded.strip() + "\n\n" + note).strip()
        result.warnings.append("解释结论等级：" + note)
    return guarded, status


def request_from_dict(payload: dict[str, Any]) -> AnalysisRequest:
    phonemes = [
        PhoneSpec(
            ipa=str(item["ipa"]),
            label=str(item.get("label", "")),
            phone_index=int(item["phone_index"]) if item.get("phone_index") is not None else None,
            reference_start=float(item.get("reference_start", 0.0)),
            reference_end=float(item.get("reference_end", 0.0)),
            learner_start=(
                float(item["learner_start"])
                if item.get("learner_start") is not None
                else None
            ),
            learner_end=(
                float(item["learner_end"])
                if item.get("learner_end") is not None
                else None
            ),
            alignment_confidence=(
                float(item["alignment_confidence"])
                if item.get("alignment_confidence") is not None
                else 1.0
            ),
            alignment_source=str(item.get("alignment_source") or ""),
        )
        for item in payload.get("phonemes", [])
    ]
    return AnalysisRequest(
        reference_object=payload["reference_object"],
        learner_object=payload["learner_object"],
        language=str(payload.get("language", "")),
        phonemes=phonemes,
        transcript=str(payload.get("transcript", "")),
        error_threshold=float(payload.get("error_threshold", 1.25)),
        maximum_errors_per_phone=int(payload.get("maximum_errors_per_phone", 3)),
        qwen_explain=bool(payload.get("qwen_explain", True)),
        qwen_vision=bool(payload.get("qwen_vision", False)),
        output_prefix=str(payload.get("output_prefix", "ai")),
        profile_id=str(payload["profile_id"]) if payload.get("profile_id") else None,
        mfa_snapshot=payload.get('mfa_snapshot'),
        selected_readings=payload.get('selected_readings', []),
        generation_source=str(payload.get('generation_source', '')),
        normalization=deepcopy(payload.get('normalization')),
        sources=deepcopy(payload.get('sources') or {}),
    )


def _resolve_request(
    bridge: PraatBridge,
    config: AppConfig,
    *,
    request_path: str | Path | None,
    user_text: str | None,
) -> tuple[AnalysisRequest, dict[str, Any]]:
    if request_path:
        payload = json.loads(Path(request_path).read_text(encoding="utf-8"))
    elif user_text:
        selected = bridge.selected_objects()
        client = QwenClient(config.qwen)
        if not client.available():
            raise QwenError(
                "Qwen is not running. Supply a request JSON file or start the local model."
            )
        payload = client.parse_analysis_request(user_text, object_payload(selected))
    else:
        raise ValueError("A request JSON path or user text is required.")

    request = request_from_dict(payload)
    reference = bridge.resolve_sound(request.reference_object)
    learner = bridge.resolve_sound(request.learner_object)
    request.reference_object = str(reference.file)
    request.learner_object = str(learner.file)
    request.sources = dict(reference=reference.to_dict(), learner=learner.to_dict())
    payload["resolved_reference"] = reference.to_dict()
    payload["resolved_learner"] = learner.to_dict()
    return request, payload


def _require_not_cancelled(cancel: threading.Event | None, message: str) -> None:
    """取消检查点：取消是「中止」，不是「失败」。"""

    if cancel is not None and cancel.is_set():
        raise AlignmentCancelled(message)


def run_tutor(
    *,
    config_path: str | Path | None = None,
    request_path: str | Path | None = None,
    user_text: str | None = None,
    request: AnalysisRequest | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> TutorOutputs:
    """跑一次纠音分析并写报告。

    ``cancel`` 会被贯穿到分析/对齐阶段（见 :func:`praat_ai.pronunciation.analyze_pronunciation`）：
    取消不会产出「已完成」的报告；如果取消发生在测量之后，已取得的测量结果照常写进报告，
    只是不再生成 AI 解释（结果保留，不丢证据）。
    """

    _require_not_cancelled(cancel, '已取消本次分析：还没有连接 Praat。')
    if request is not None and request.normalization is not None:
        request = deepcopy(request)  # Freeze before callbacks/model startup can change the caller's selection.

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    report(0.08, "读取请求和 Praat 选中对象")
    config = load_config(config_path)
    bridge = PraatBridge.from_praat()
    if request is None:
        request, request_payload = _resolve_request(
            bridge,
            config,
            request_path=request_path,
            user_text=user_text,
        )
    else:
        reference = bridge.resolve_sound(request.reference_object)
        learner = bridge.resolve_sound(request.learner_object)
        request.reference_object = str(reference.file)
        request.learner_object = str(learner.file)
        request.sources = dict(reference=reference.to_dict(), learner=learner.to_dict())
        request_payload = request.to_dict()
        request_payload["resolved_reference"] = reference.to_dict()
        request_payload["resolved_learner"] = learner.to_dict()

    resolve_profile(request.profile_id, request.language)
    gpu = detect_gpu()
    profile = select_runtime_profile(
        gpu.free_mb if gpu else None,
        request.qwen_vision and config.qwen.vision_when_requested,
    )
    config.qwen.max_context_tokens = profile.context_tokens

    server = QwenServerManager(config, profile)
    server_started = False
    server_error = ""
    try:
        if request.qwen_explain or request.qwen_vision:
            try:
                report(0.18, "启动前端模型")
                server_started = server.ensure_started()
            except QwenServerError as error:
                server_error = str(error)

        report(0.32, "音素对齐与声学特征提取")
        with task_alignment(request, config.alignment) as task_config:
            result = analyze_pronunciation(request, task_config, cancel=cancel)
        if request.mfa_snapshot:
            result.mfa_snapshot = request.mfa_snapshot
            result.selected_readings = request.selected_readings
            result.generation_source = request.generation_source
        report(0.68, "汇总音位判据与证据状态" if request.profile_id else "生成偏差评分")
        output_dir = bridge.output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = re.sub(r"[^\w.-]+", "_", request.output_prefix).strip("._") or "ai"
        reports_root = Path(
            os.getenv("PRAAT_AI_REPORT_DIR") or installation_root() / "reports"
        ).expanduser().resolve()
        if reports_root.is_relative_to(output_dir):
            raise ValueError(
                "PRAAT_AI_REPORT_DIR cannot be inside temporary Praat output "
                f"directory: {output_dir}"
            )
        reports_root.mkdir(parents=True, exist_ok=True)
        report_dir = Path(tempfile.mkdtemp(prefix=f"{prefix}_", dir=reports_root))

        json_path = report_dir / f"{prefix}_report.json"
        textgrid_path = report_dir / f"{prefix}_errors.TextGrid"
        overlay_path = report_dir / f"{prefix}_overlay.png"

        if cancel is not None and cancel.is_set():
            # 测量已经拿到：取消只停掉「写解释」这一步，报告照样交付，缺的那一步如实写明。
            result.explanation = "已取消：测量结果已保留，未生成 AI 解释。"
            result.explanation_status = "cancelled"
            result.warnings.append("已取消：未生成 AI 解释；下面的测量结果在取消前已经完成。")
        elif request.qwen_explain:
            client = QwenClient(config.qwen)
            if client.available():
                try:
                    report(0.76, "生成前端解释")
                    explanation = client.explain_errors(
                        explanation_payload(request_payload),
                        explanation_payload(result.to_dict()),
                    )
                    result.explanation, result.explanation_status = guard_explanation(
                        result, explanation, request_payload=request_payload)
                except QwenError as error:
                    result.explanation = f"Qwen 解释不可用：{error}"
                    result.explanation_status = "unavailable"
            elif server_error:
                result.explanation = f"Qwen 解释不可用：{server_error}"
                result.explanation_status = "unavailable"

        report(0.86, "生成 TextGrid、JSON 和叠加图")
        write_textgrid(result, textgrid_path)
        shutil.copyfile(textgrid_path, output_dir / textgrid_path.name)
        write_json(result, json_path)
        rendered = write_overlay_png(result, overlay_path)
        if rendered:
            shutil.copyfile(overlay_path, output_dir / overlay_path.name)

        if request.qwen_vision and rendered:
            client = QwenClient(config.qwen)
            if client.available():
                try:
                    report(0.94, "生成视觉解释")
                    vision_explanation = client.vision_explain(
                        overlay_path,
                        explanation_payload(request_payload),
                        explanation_payload(result.to_dict()),
                    )
                    if vision_explanation:
                        guarded, status = guard_explanation(
                            result, vision_explanation, request_payload=request_payload)
                        result.explanation = (
                            result.explanation + "\n\n图表解释：\n" + guarded
                        ).strip()
                        if status == "rejected_masked":
                            result.explanation_status = "rejected_masked"
                        write_json(result, json_path)
                except QwenError as error:
                    # 视觉解释失败以前被静默丢掉；这里留原因，并重写报告让它真的落到交付物里。
                    result.warnings.append(f"视觉解释未生成：{error}")
                    write_json(result, json_path)

        report(1.0, "分析完成")
        return TutorOutputs(
            json_path=json_path,
            textgrid_path=textgrid_path,
            overlay_path=overlay_path if rendered else None,
            result=result,
            request_payload=request_payload,
        )
    finally:
        if server_started and profile.unload_after_sec == 0:
            server.stop()


def run_from_praat(
    *,
    config_path: str | Path | None = None,
    request_path: str | Path | None = None,
    user_text: str | None = None,
    interactive: bool = True,
) -> int:
    request_path = request_path or os.getenv("PRAAT_AI_REQUEST_JSON")
    user_text = user_text or os.getenv("PRAAT_AI_USER_TEXT")
    request = None
    if not request_path and not user_text and interactive:
        bridge = PraatBridge.from_praat()
        sounds = [
            item
            for item in bridge.selected_objects()
            if item.class_name == "Sound"
        ]
        try:
            values = show_tutor_form(sounds, config_path=config_path)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        except Exception as error:
            from .desktop_launch import report_failure
            return report_failure(error, config_path=config_path)
        request = values.request
        if request is None:
            return 0
    try:
        outputs, cancelled = run_with_stop(
            lambda cancel, report: run_tutor(
                config_path=config_path,
                request_path=request_path,
                user_text=user_text,
                request=request,
                progress=report,
                cancel=cancel,
            ),
            enabled=interactive,
        )
    except AlignmentCancelled as error:
        print(f"已停止分析：{error}", file=sys.stderr)
        print("本次没有生成报告；如需报告请重新运行并让它走完。", file=sys.stderr)
        return 3
    except ValueError as error:
        print(f"无法开始 AI 纠音：{error}", file=sys.stderr)
        return 1
    if interactive:
        try:
            from .tutor_report import queue_report
            import start_ai_chat
            queue_report(outputs)
            if start_ai_chat.main() == 0:
                return 0
            print('报告已保存，AI 工作台未能打开。')
        except Exception as error:
            print(f'报告已保存，显示失败：{error}', file=sys.stderr)
    if outputs.result.profile_snapshot:
        print(f"音位声学对比完成，生成 {len(outputs.result.observations)} 项观察（实验性）。")
    else:
        print(f"旧式声学对比完成，发现 {len(outputs.result.errors)} 个偏差。")
    print(
        f"对齐：{outputs.result.alignment_source or 'unknown'}，"
        f"置信度 {outputs.result.alignment_confidence:.2f}"
    )
    print(f"报告（JSON 文件）：{outputs.json_path}")
    print(f"标注（TextGrid 文件）：{outputs.textgrid_path}")
    if outputs.overlay_path:
        print(f"叠加图（PNG 文件）：{outputs.overlay_path}")
    if cancelled:
        # 取消发生在测量之后：结果已经写进上面那份报告，缺的只有 AI 解释。
        print("已停止：不再生成 AI 解释；取消前完成的测量结果照常写进上面这份报告。")
    if outputs.result.explanation:
        print("")
        print(outputs.result.explanation)
    return 0
