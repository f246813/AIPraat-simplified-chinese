from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path
from typing import Any
import numpy as np

from .models import FeatureTrack, PhoneFeatureTracks, PhoneSpec


@dataclass(slots=True)
class SoundSamples:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(len(self.samples)) / self.sample_rate


def read_wav(path: str | Path) -> SoundSamples:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            raw = handle.readframes(frame_count)
    except (wave.Error, EOFError) as error:
        raise ValueError(f"无法读取 WAV 音频 {Path(path).name}：文件不是有效的 PCM WAV，"
                         "请检查 Praat 对象导出结果。") from error

    if sample_width == 1:
        values = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        values = (values - 128.0) / 128.0
    elif sample_width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sample_width == 4:
        values = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width * 8} bit")

    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)

    return SoundSamples(samples=values, sample_rate=sample_rate)


def _slice(sound: SoundSamples, start: float, end: float) -> np.ndarray:
    start_index = max(0, int(start * sound.sample_rate))
    end_index = min(len(sound.samples), int(end * sound.sample_rate))
    if end_index <= start_index:
        return np.zeros(0, dtype=np.float64)
    return sound.samples[start_index:end_index]


def _frame_bounds(
    sample_count: int,
    sample_rate: int,
    frame_sec: float = 0.025,
    hop_sec: float = 0.01,
) -> tuple[int, int]:
    frame_size = max(16, int(frame_sec * sample_rate))
    hop_size = max(1, int(hop_sec * sample_rate))
    if sample_count < frame_size:
        frame_size = sample_count
    return frame_size, hop_size


def _basic_tracks(
    segment: np.ndarray,
    sample_rate: int,
    offset: float,
) -> dict[str, FeatureTrack]:
    if segment.size == 0:
        return {}

    frame_size, hop_size = _frame_bounds(len(segment), sample_rate)
    if frame_size <= 0:
        return {}

    window = np.hanning(frame_size)
    times: list[float] = []
    rms_values: list[float] = []
    zcr_values: list[float] = []
    centroid_values: list[float] = []
    high_ratio_values: list[float] = []
    frequencies = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    high_frequency_mask = frequencies >= 2000.0

    for start in range(0, max(1, len(segment) - frame_size + 1), hop_size):
        frame = segment[start : start + frame_size]
        if frame.size < frame_size:
            break
        windowed = frame * window
        rms = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        signs = np.signbit(frame)
        zcr = float(np.mean(signs[1:] != signs[:-1])) if frame.size > 1 else 0.0
        spectrum = np.abs(np.fft.rfft(windowed))
        spectrum_sum = float(np.sum(spectrum) + 1e-12)
        centroid = float(np.sum(frequencies * spectrum) / spectrum_sum)
        high_ratio = float(np.sum(spectrum[high_frequency_mask]) / spectrum_sum)
        times.append(offset + (start + frame_size / 2.0) / sample_rate)
        rms_values.append(rms)
        zcr_values.append(zcr)
        centroid_values.append(centroid)
        high_ratio_values.append(high_ratio)

    return {
        "rms": FeatureTrack("rms", times, rms_values, "amplitude", 0.8, 0.04),
        "zcr": FeatureTrack("zcr", times, zcr_values, "ratio", 0.7, 0.05),
        "spectral_centroid": FeatureTrack(
            "spectral_centroid",
            times,
            centroid_values,
            "Hz",
            1.0,
            500.0,
        ),
        "high_frequency_ratio": FeatureTrack(
            "high_frequency_ratio",
            times,
            high_ratio_values,
            "ratio",
            0.9,
            0.08,
        ),
    }


class _ParselmouthAnalysis:
    """一段音频只打开一次的 Pitch/Intensity/Formant 视图。

    旧实现每个音素都重新 ``parselmouth.Sound(path)`` 并重建这三个对象：音素一多，
    同一段音频被反复读取和重建（实测就是这个问题）。这里把构造提到循环外，
    每个音素只按自己的时间点取样。
    """

    def __init__(self, pitch: Any, intensity: Any, formant: Any) -> None:
        self._pitch = pitch
        self._intensity = intensity
        self._formant = formant

    def tracks(self, times: list[float]) -> dict[str, FeatureTrack]:
        if not times:
            return {}
        pitch_values = [float(self._pitch.get_value_at_time(time)) for time in times]
        intensity_values = [float(self._intensity.get_value(time)) for time in times]
        return {
            "pitch": FeatureTrack("pitch", times, pitch_values, "Hz", 0.9, 18.0),
            "intensity": FeatureTrack("intensity", times, intensity_values, "dB", 0.8, 4.0),
            "f1": FeatureTrack("f1", times, [float(self._formant.get_value_at_time(1, time)) for time in times],
                               "Hz", 1.3, 120.0),
            "f2": FeatureTrack("f2", times, [float(self._formant.get_value_at_time(2, time)) for time in times],
                               "Hz", 1.3, 180.0),
            "f3": FeatureTrack("f3", times, [float(self._formant.get_value_at_time(3, time)) for time in times],
                               "Hz", 0.9, 220.0),
        }


def _open_parselmouth(
    path: str | Path,
    pitch_settings: dict | None = None,
) -> tuple[_ParselmouthAnalysis | None, str, str]:
    """打开一次音频，返回 ``(分析视图, 状态, 原因)``。

    状态取值：``ok`` / ``dependency_missing``（解释器里没有 praat-parselmouth）/
    ``unavailable``（音频太短，做不了基频/共振峰）/ ``extraction_failed``（装了但提取失败）。
    旧实现把所有异常都吞成 ``{}``，于是「没装依赖」「提取失败」和「真的没测到差异」
    在交付物里长得一样；这里把状态和原因一起返回，交给上层如实写进报告。
    """

    try:
        import parselmouth  # type: ignore[import-not-found]
    except ImportError:
        # 缺依赖的原因与「去哪儿装」统一到 capabilities，避免同一件事两处说法不一致。
        from . import capabilities
        detail = capabilities.blocker("pitch") or (
            "当前解释器没有 praat-parselmouth（requirements 对 Python 3.13 及以上不安装）"
        )
        return (None, "dependency_missing",
                detail + "本次只有频谱类特征，没有基频/共振峰。")

    try:
        sound = parselmouth.Sound(str(path))
        if sound.duration < 0.02:
            return (None, "unavailable",
                    f"音频时长 {sound.duration:.4f} 秒不足 0.02 秒，未做基频/共振峰提取。")
        settings_pitch = pitch_settings or {"floor_hz": 75.0, "ceiling_hz": 600.0}
        pitch = sound.to_pitch(time_step=0.01, pitch_floor=settings_pitch['floor_hz'],
                               pitch_ceiling=settings_pitch['ceiling_hz'])
        intensity = sound.to_intensity(minimum_pitch=50.0, time_step=0.01)
        from .normalization import MEASUREMENT_SETTINGS as settings
        formant = sound.to_formant_burg(
            time_step=settings['time_step_sec'], max_number_of_formants=settings['max_number_of_formants'],
            maximum_formant=settings['maximum_formant_hz'], window_length=settings['window_length_sec'],
            pre_emphasis_from=settings['pre_emphasis_from_hz'],
        )
        return _ParselmouthAnalysis(pitch, intensity, formant), "ok", ""
    except Exception as error:  # 提取失败本身要可见，不再静默返回空特征。
        return (None, "extraction_failed",
                f"基频/共振峰提取失败：{type(error).__name__}: {error}")


def extract_phone_tracks(
    path: str | Path,
    phones: list[PhoneSpec],
    *,
    learner: bool,
    pitch_settings: dict | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[PhoneFeatureTracks]:
    sound = read_wav(path)
    analysis, status, reason = _open_parselmouth(path, pitch_settings)
    result: list[PhoneFeatureTracks] = []
    for index, phone in enumerate(phones, start=1):
        start = phone.learner_start if learner else phone.reference_start
        end = phone.learner_end if learner else phone.reference_end
        if start is None or end is None:
            continue
        segment = _slice(sound, start, end)
        tracks = _basic_tracks(segment, sound.sample_rate, start)
        fallback_times = tracks.get("rms").times if tracks.get("rms") else []
        if analysis is not None:
            tracks.update(analysis.tracks(fallback_times))
        result.append(
            PhoneFeatureTracks(
                phone_index=phone.phone_index if phone.phone_index is not None else index,
                ipa=phone.ipa,
                start=start,
                end=end,
                tracks=tracks,
                alignment_confidence=phone.alignment_confidence,
                alignment_source=phone.alignment_source,
            )
        )
    if diagnostics is not None:
        diagnostics.update(
            pitch_formant_status=status,
            pitch_formant_reason=reason,
            pitch_formant_extractions=1 if analysis is not None else 0,
        )
    return result
