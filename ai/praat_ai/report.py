from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from .models import AnalysisResult, PronunciationError


def write_json(result: AnalysisResult, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _escape_textgrid(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _assessment_gap(assessment: dict) -> str:
    """某个音位上「没测成」的原因（去重后连成一句），没有就返回空串。"""

    reasons = [
        str(check.get("reason") or "").strip()
        for check in assessment.get("checks") or []
        if check.get("status") == "insufficient_evidence"
    ]
    return "；".join(dict.fromkeys(reason for reason in reasons if reason))


def write_textgrid(result: AnalysisResult, path: str | Path) -> None:
    if result.profile_snapshot:
        _write_rule_textgrid(result, path)
        return
    errors_by_phone: dict[int, list[PronunciationError]] = defaultdict(list)
    for error in result.errors:
        errors_by_phone[error.phone_index].append(error)

    intervals: list[tuple[float, float, str]] = []
    assessments = {item["phone_index"]: item for item in result.phone_assessments}
    for phone in result.learner_phones:
        phone_errors = errors_by_phone.get(phone.phone_index, [])
        assessment = assessments.get(phone.phone_index) or {}
        status = str(assessment.get("status") or "")
        gap = _assessment_gap(assessment)
        if phone_errors:
            label = "；".join(error.message for error in phone_errors)
            if status == "partial" and gap:
                # 有比过的、也有没比成的：结论文字后面必须写清哪部分没测。
                label += "（本次另有未测量项：" + gap + "）"
        elif status in {"insufficient_evidence", "not_evaluated"}:
            label = "证据不足：" + (gap or "本次没有取得可比较的声学特征") + "，未评估"
        elif not assessment:
            # 旧式对比对一个音位什么都没记：那一次根本没法评估，不能说「没有差异」。
            label = "证据不足：本次没有该音位的对比记录（未测量），未评估"
        elif status == "partial" and gap:
            label = "本次只有部分特征可测量（未测量项：" + gap + "），未发现其余特征超过旧式声学对比阈值的差异"
        else:
            label = "未发现超过旧式声学对比阈值的差异"
        intervals.append((phone.start, phone.end, label))

    if any(not (math.isfinite(start) and math.isfinite(end) and
                0 <= start < end <= result.duration) for start, end, _ in intervals):
        intervals = [(0.0, result.duration, "证据不足：音素区间无效或超出音频范围，未评估")]
    if not intervals:
        intervals.append((0.0, result.duration, "无可用音素区间"))
    intervals.sort(key=lambda item: item[0])
    intervals[0] = (0.0, intervals[0][1], intervals[0][2])
    intervals[-1] = (intervals[-1][0], result.duration, intervals[-1][2])

    lines = [
        'File type = "ooTextFile"',
        'Object class = "TextGrid"',
        "",
        "xmin = 0",
        f"xmax = {result.duration:.6f}",
        "tiers? <exists>",
        "size = 2",
        "item []:",
        "    item [1]:",
        '        class = "IntervalTier"',
        '        name = "AI_Errors"',
        "        xmin = 0",
        f"        xmax = {result.duration:.6f}",
        f"        intervals: size = {len(intervals)}",
    ]
    for index, (start, end, label) in enumerate(intervals, start=1):
        lines.extend(
            [
                f"        intervals [{index}]:",
                f"            xmin = {start:.6f}",
                f"            xmax = {end:.6f}",
                f"            text = {_escape_textgrid(label)}",
            ]
        )

    points_by_time: dict[float, list[PronunciationError]] = defaultdict(list)
    for error in result.errors:
        center = round((error.start + error.end) / 2.0, 6)
        points_by_time[center].append(error)

    lines.extend(
        [
            "    item [2]:",
            '        class = "TextTier"',
            '        name = "AI_ErrorFeatures"',
            "        xmin = 0",
            f"        xmax = {result.duration:.6f}",
            f"        points: size = {len(points_by_time)}",
        ]
    )
    for index, (center, point_errors) in enumerate(
        sorted(points_by_time.items()), start=1
    ):
        marks = dict.fromkeys(
            f"/{error.ipa}/ {error.feature} {error.direction}"
            for error in point_errors
        )
        mark = "；".join(marks)
        lines.extend(
            [
                f"        points [{index}]:",
                f"            number = {center:.6f}",
                f"            mark = {_escape_textgrid(mark)}",
            ]
        )

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_acoustic_tiers(lines, result):
    tiers = []
    for phone in result.learner_phones:
        measure = phone.measurements.get("vot")
        if not measure:
            continue
        acoustic = measure.analysis.get("acoustic", {})
        points = defaultdict(list)
        for event in acoustic.get("events", []):
            time = event.get("time")
            if time is not None and 0 <= time <= result.duration and event.get("status") != "rejected":
                points[time].append(f"{event['kind']} [{event['status']}]")
        tiers.append(('TextTier', f"VOT_{phone.phone_index}_events", sorted(points.items())))
        for interval in acoustic.get("intervals", []):
            start, end = interval["start"], interval["end"]
            if 0 <= start < end <= result.duration:
                segments = [(0.0, start, ""), (start, end, f"{interval['kind']} [{interval['status']}]"),
                            (end, result.duration, "")]
                tiers.append(('IntervalTier', f"VOT_{phone.phone_index}_{interval['id']}",
                              [s for s in segments if s[0] < s[1]]))
    count_line = next(i for i, line in enumerate(lines) if line.startswith("size = "))
    count = int(lines[count_line].split("=")[1])
    lines[count_line] = f"size = {count + len(tiers)}"
    for tier, (kind, name, entries) in enumerate(tiers, count+1):
        lines.extend([f"    item [{tier}]:", f'        class = "{kind}"',
            f"        name = {_escape_textgrid(name)}", "        xmin = 0", f"        xmax = {result.duration:.9f}",
            f"        {'points' if kind == 'TextTier' else 'intervals'}: size = {len(entries)}"])
        for index, entry in enumerate(entries, 1):
            if kind == 'TextTier':
                time, labels = entry
                lines.extend([f"        points [{index}]:", f"            number = {time:.9f}",
                              f"            mark = {_escape_textgrid('; '.join(labels))}"])
            else:
                start, end, label = entry
                lines.extend([f"        intervals [{index}]:", f"            xmin = {start:.9f}",
                              f"            xmax = {end:.9f}", f"            text = {_escape_textgrid(label)}"])


def _write_rule_textgrid(result: AnalysisResult, path: str | Path) -> None:
    """Keep true learner boundaries, including unlabelled gaps, in new reports."""
    assessments = {item.get("learner_phone_index", item["phone_index"]): item
                   for item in result.phone_assessments
                   if item.get("learner_phone_index", item["phone_index"]) is not None}
    sequence_mismatch = len(result.reference_phones) != len(result.learner_phones)
    labels = {"observed": "已生成声学观察（实验性）", "partial": "部分可评估；证据不足项见 JSON",
              "duration_only": "仅生成时长观察（实验性）",
              "insufficient_evidence": "证据不足", "not_evaluated": "未评估"}
    phones = sorted(result.learner_phones, key=lambda phone: phone.start)
    valid = all(math.isfinite(phone.start) and math.isfinite(phone.end) and
                0 <= phone.start < phone.end <= result.duration for phone in phones)
    valid = valid and all(left.end <= right.start for left, right in zip(phones, phones[1:]))
    lines = ['File type = "ooTextFile"', 'Object class = "TextGrid"', "", "xmin = 0",
             f"xmax = {result.duration:.6f}", "tiers? <exists>", "size = 4", "item []:"]
    for tier, name in enumerate(("Target_Phones", "AI_ProblemCandidates", "AI_EvidenceStatus"), start=1):
        intervals = []
        cursor = 0.0
        gap_status = "证据不足" if tier == 3 and sequence_mismatch else "未评估"
        if valid:
            for phone in phones:
                if phone.start > cursor:
                    intervals.append((cursor, phone.start, gap_status))
                assessment = assessments.get(phone.phone_index, {})
                if assessment and assessment.get("ipa") != phone.ipa:
                    assessment = {}
                status = labels.get(assessment.get("status"), "未评估")
                if not assessment and (sequence_mismatch or result.phone_assessments):
                    status = "证据不足"
                text = phone.ipa if tier == 1 else ("未评估：未执行独立音素识别" if tier == 2 else status)
                intervals.append((phone.start, phone.end, text))
                cursor = phone.end
        if cursor < result.duration:
            intervals.append((cursor, result.duration, gap_status if valid else "证据不足：区间无效或重叠"))
        lines.extend([f"    item [{tier}]:", '        class = "IntervalTier"',
            f"        name = {_escape_textgrid(name)}", "        xmin = 0", f"        xmax = {result.duration:.6f}",
            f"        intervals: size = {len(intervals)}"])
        for index, (start, end, text) in enumerate(intervals, start=1):
            lines.extend([f"        intervals [{index}]:", f"            xmin = {start:.6f}",
                f"            xmax = {end:.6f}", f"            text = {_escape_textgrid(text)}"])
    grouped = defaultdict(list)
    if valid:
        for observation in result.observations:
            grouped[round((observation.start + observation.end) / 2, 6)].append(observation)
    lines.extend(['    item [4]:', '        class = "TextTier"', '        name = "AI_Observations"',
                  "        xmin = 0", f"        xmax = {result.duration:.6f}", f"        points: size = {len(grouped)}"])
    for index, (time, observations) in enumerate(sorted(grouped.items()), start=1):
        text = "；".join(observation.message for observation in observations)
        lines.extend([f"        points [{index}]:", f"            number = {time:.6f}", f"            mark = {_escape_textgrid(text)}"])
    _append_acoustic_tiers(lines, result)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_overlay_png(result: AnalysisResult, path: str | Path) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    width = 1280
    row_height = 150
    header_height = 60
    candidates = result.observations if result.profile_snapshot else [*result.errors, *result.observations]
    if result.normalization and result.profile_snapshot:
        # Expose each actual method, rather than hiding all prosody behind the
        # first few raw formant observations. Repeated phone/run items stay in JSON/TG.
        selected_errors, seen = [], set()
        ordered = sorted(candidates, key=lambda item: not any(item.feature in p.derived_tracks for p in result.reference_phones))
        for item in ordered:
            if item.feature not in seen:
                selected_errors.append(item)
                seen.add(item.feature)
        selected_errors = selected_errors[:12]
    else:
        selected_errors = candidates[:6]
    plot_duration = max(result.duration, max((p.end for p in result.reference_phones), default=0), 1e-9)
    height = header_height + max(1, len(selected_errors)) * row_height + 30
    image = Image.new("RGB", (width, height), "#111827")
    draw = ImageDraw.Draw(image)
    draw.text((20, 18), "Experimental phone-specific observations" if result.profile_snapshot else "Praat AI pronunciation comparison", fill="#f9fafb")

    reference_by_index = {item.phone_index: item for item in result.reference_phones}
    learner_by_index = {item.phone_index: item for item in result.learner_phones}
    if result.profile_snapshot:
        learner_by_index = {reference.phone_index: learner for reference, learner
                            in zip(result.reference_phones, result.learner_phones)}

    if not selected_errors:
        draw.text((20, header_height + 20), "Not evaluated: see JSON evidence status." if result.profile_snapshot else "No legacy comparison threshold violations.", fill="#86efac")
        image.save(path)
        return True

    for row, error in enumerate(selected_errors):
        top = header_height + row * row_height
        bottom = top + row_height - 18
        draw.rectangle((15, top, width - 15, bottom), fill="#1f2937")
        draw.text(
            (25, top + 8),
            f"/{error.ipa}/ {error.feature} [{error.unit}] " + ("observation (experimental)" if result.profile_snapshot else f"score={error.score:.2f} {error.direction}"),
            fill="#fca5a5",
        )
        draw.text((650, top + 8), f"ref={error.reference_value:.3f}{error.unit}  learner={error.learner_value:.3f}{error.unit}", fill="#d1d5db")
        reference_phone = reference_by_index.get(error.phone_index)
        learner_phone = learner_by_index.get(error.phone_index)
        if not reference_phone or not learner_phone:
            continue
        if error.feature in reference_phone.measurements:
            measure = reference_phone.measurements[error.feature]
            draw.text((40, top + 48), f"landmark-derived scalar [{error.evaluation_status}]", fill="#d1d5db")
            draw.text((40, top + 76), str(measure.analysis.get('measurement_id', '')), fill="#d1d5db")
            continue
        if error.feature == "relative_duration":
            scale = max(error.reference_value, error.learner_value, 1e-9)
            for value, y, color in ((error.reference_value, top + 38, "#60a5fa"), (error.learner_value, top + 80, "#f59e0b")):
                draw.rectangle((40, y, 40 + int((width - 160) * value / scale), y + 20), fill=color)
                draw.text((45, y), f"{value:.3f} relative duration", fill="#ffffff")
            continue
        derived = error.feature in reference_phone.derived_tracks
        reference_track = (reference_phone.derived_tracks if derived else reference_phone.tracks).get(error.feature)
        learner_track = (learner_phone.derived_tracks if derived else learner_phone.tracks).get(error.feature)
        if not reference_track or not learner_track:
            continue

        all_values = [value for value in reference_track.values + learner_track.values if math.isfinite(value)]
        if not all_values:
            continue
        minimum = min(all_values)
        maximum = max(all_values)
        value_range = max(maximum - minimum, 1e-9)

        def points(track, start: float, end: float):
            track_values = track.values
            times = track.times if len(track.times) == len(track_values) else [
                start + (end - start) * index / max(len(track_values) - 1, 1) for index in range(len(track_values))]
            total = plot_duration
            return [
                (
                    int(40 + (time / total) * (width - 80)),
                    int(bottom - 20 - ((value - minimum) / value_range) * (bottom - top - 55)),
                )
                if math.isfinite(value) and math.isfinite(time) else None
                for time, value in zip(times, track_values)
            ]

        reference_points = points(
            reference_track,
            reference_phone.start,
            reference_phone.end,
        )
        learner_points = points(
            learner_track,
            learner_phone.start,
            learner_phone.end,
        )
        for track_points, color in ((reference_points, "#60a5fa"), (learner_points, "#f59e0b")):
            run = []
            for point in [*track_points, None]:
                if point is not None:
                    run.append(point)
                else:
                    if len(run) > 1:
                        draw.line(run, fill=color, width=3)
                    run = []

        total = plot_duration
        left = int(40 + (error.start / total) * (width - 80))
        right = int(40 + (error.end / total) * (width - 80))
        draw.rectangle((left, top + 30, right, bottom - 5), outline="#ef4444", width=3)
        draw.text((width - 260, top + 8), "reference  learner", fill="#d1d5db")

    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("acoustic_measurements", json.dumps(dict(protocol=result.measurement_protocol,
        ids=[item.evidence.get("measurement_id") for item in selected_errors], sources=result.sources), ensure_ascii=False))
    image.save(path, pnginfo=info)
    return True
