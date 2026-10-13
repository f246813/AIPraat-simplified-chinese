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
        label = "未发现超过旧式声学对比阈值的差异" if not phone_errors else "；".join(
            error.message for error in phone_errors
        )
        if assessments.get(phone.phone_index, {}).get("status") == "insufficient_evidence":
            label = "证据不足：参考或学习者区间无效或超出音频范围，未评估"
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
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_overlay_png(result: AnalysisResult, path: str | Path) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    width = 1280
    row_height = 150
    header_height = 60
    selected_errors = (result.observations if result.profile_snapshot else result.errors)[:6]
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
            f"/{error.ipa}/ {error.feature} " + ("observation (experimental)" if result.profile_snapshot else f"score={error.score:.2f} {error.direction}"),
            fill="#fca5a5",
        )
        reference_phone = reference_by_index.get(error.phone_index)
        learner_phone = learner_by_index.get(error.phone_index)
        if not reference_phone or not learner_phone:
            continue
        if error.feature == "relative_duration":
            scale = max(error.reference_value, error.learner_value, 1e-9)
            for value, y, color in ((error.reference_value, top + 38, "#60a5fa"), (error.learner_value, top + 80, "#f59e0b")):
                draw.rectangle((40, y, 40 + int((width - 160) * value / scale), y + 20), fill=color)
                draw.text((45, y), f"{value:.3f} relative duration", fill="#ffffff")
            continue
        reference_track = reference_phone.tracks.get(error.feature)
        learner_track = learner_phone.tracks.get(error.feature)
        if not reference_track or not learner_track:
            continue

        all_values = [value for value in reference_track.values + learner_track.values if math.isfinite(value)]
        if not all_values:
            continue
        minimum = min(all_values)
        maximum = max(all_values)
        value_range = max(maximum - minimum, 1e-9)

        def points(track_values: list[float], start: float, end: float) -> list[tuple[int, int]]:
            if len(track_values) == 1:
                times = [start, end]
                track_values = track_values * 2
            else:
                times = [
                    start + (end - start) * index / (len(track_values) - 1)
                    for index in range(len(track_values))
                ]
            total = max(result.duration, 1e-9)
            return [
                (
                    int(40 + (time / total) * (width - 80)),
                    int(bottom - 20 - ((value - minimum) / value_range) * (bottom - top - 55)),
                )
                if math.isfinite(value) else None
                for time, value in zip(times, track_values)
            ]

        reference_points = points(
            reference_track.values,
            reference_phone.start,
            reference_phone.end,
        )
        learner_points = points(
            learner_track.values,
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

        total = max(result.duration, 1e-9)
        left = int(40 + (error.start / total) * (width - 80))
        right = int(40 + (error.end / total) * (width - 80))
        draw.rectangle((left, top + 30, right, bottom - 5), outline="#ef4444", width=3)
        draw.text((width - 260, top + 8), "reference  learner", fill="#d1d5db")

    image.save(path)
    return True
