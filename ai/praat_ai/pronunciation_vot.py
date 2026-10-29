"""Pronunciation stop measurements using the chat VOT script and receipt contract."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import subprocess
import tempfile

from .acoustic_events import consume_vot_receipt
from .measurement_protocol import VERSION, identity
from .models import ScalarMeasurement
from .pronunciation_rules import phone_class, _located


def extract_stop_measurements(path, phones, *, language, duration, source, cancel=None):
    from . import tools
    from .forced_alignment import AlignmentCancelled
    executable = Path(os.environ.get("PRAAT_AI_PRAAT_EXECUTABLE") or Path(__file__).resolve().parents[2] / "Praat.exe")
    for index, phone in enumerate(phones):
        if phone_class(phone.ipa, language) != "stop":
            continue
        if cancel is not None and cancel.is_set():
            raise AlignmentCancelled("已取消：未继续检测塞音地标。")
        reason = ""
        acoustic = None
        lo = phones[index-1].start if index else 0.0
        hi = phones[index+1].end if index+1 < len(phones) else duration
        if not _located(phone) or not 0 <= lo <= phone.start < phone.end <= hi <= duration:
            reason = "目标塞音或相邻上下文缺少可靠有效定位，未估造 VOT。"
        elif index and not _located(phones[index-1]) or index+1 < len(phones) and not _located(phones[index+1]):
            reason = "相邻音位定位不可靠，不能限定闭塞与起浊搜索范围。"
        elif not executable.is_file():
            reason = "缺少真实 Praat 可执行程序，未执行 VOT 候选检测。"
        else:
            try:
                with tempfile.TemporaryDirectory(prefix="aipraat-vot-") as temporary:
                    folder = Path(temporary)
                    context = tools.ToolContext((tools.ObjectRow(1, "Sound", "target", True),),
                                                folder / "result.tsv", folder / "state.txt")
                    arguments = {"object": 1, "from": lo, "to": hi}
                    script = folder / "measure.praat"
                    script.write_text("Read from file: " + tools.quote(Path(path).resolve()) + "\n" +
                                      tools.render("vot", arguments, context), encoding="utf-8")
                    process = subprocess.Popen([str(executable), "--no-pref-files", "--no-plugins", "--FULL-TRUST",
                                                "--run", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        for _ in range(300):
                            try:
                                stdout, stderr = process.communicate(timeout=.1)
                                break
                            except subprocess.TimeoutExpired:
                                if cancel is not None and cancel.is_set():
                                    raise AlignmentCancelled("已取消塞音地标检测。")
                        else:
                            raise TimeoutError("VOT 检测超过 30 秒")
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()
                    if process.returncode:
                        raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "Praat 检测失败")
                    rows = context.result_path.read_text(encoding="utf-8-sig").splitlines()
                    acoustic = consume_vot_receipt(rows, arguments, source)
                    if acoustic is None:
                        raise ValueError("未收到声学事件回执")
                    acoustic.method = dict(protocol="chat-vot-candidates-v1", engine="native-praat",
                        minimum_periods=3, minimum_duration_sec=.02, burst_rise_db=6,
                        pitch_floor_hz=75, pitch_top_hz=600,
                        search="target_with_adjacent_phone_context", target_scope=[phone.start, phone.end])
                    burst = next(e for e in acoustic.events if e.id == "burst")
                    if burst.time is not None and not phone.start <= burst.time <= phone.end:
                        for measure in acoustic.measures:
                            measure.value, measure.status = None, "unavailable"
                            measure.reason = "释放候选位于目标塞音定位区间外，不能归给目标塞音"
                        acoustic.checks.append(dict(rule="target.release_association", status="uncertain",
                                                    reason="搜索范围包含相邻音，释放归属未确认"))
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                reason = "VOT 候选检测未完成：" + str(error)
        payload = acoustic.to_dict() if acoustic is not None else dict(source=deepcopy(source), scope=[lo, hi],
            events=[], intervals=[], checks=[], method={}, schema="acoustic-analysis-v1", kind="vot")
        measures = acoustic.measures if acoustic is not None else []
        for key, name in (("vot", "vot_ms"), ("burst_to_periodicity_ms", "burst_to_periodicity_ms"),
                          ("burst_to_clear_vowel_ms", "burst_to_clear_vowel_ms")):
            measure = next((m for m in measures if m.name == name), None)
            metadata = dict(measurement_protocol=VERSION, protocol="vot-landmarks-v1",
                quantity_type="landmark_derived_scalar", source=deepcopy(source),
                phone_index=phone.phone_index, ipa=phone.ipa, requested_scope=[lo, hi],
                formula=measure.formula if measure else "(voicing_onset - release_burst) * 1000" if key == "vot" else "",
                acoustic=payload)
            metadata["measurement_id"] = identity(dict(source=source, name=name, scope=[lo, hi],
                phone_index=phone.phone_index, protocol="vot-landmarks-v1", acoustic=payload))
            phone.measurements[key] = ScalarMeasurement(name, measure.value if measure else None, "ms",
                measure.status if measure else "unavailable", measure.reason if measure else reason, metadata)
