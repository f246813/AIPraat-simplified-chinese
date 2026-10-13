from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

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
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        raw = handle.readframes(frame_count)

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


def _parselmouth_tracks(
    path: str | Path,
    start: float,
    end: float,
    fallback_times: list[float],
) -> dict[str, FeatureTrack]:
    try:
        import parselmouth  # type: ignore[import-not-found]
    except ImportError:
        return {}

    try:
        sound = parselmouth.Sound(str(path))
        if sound.duration < 0.02:
            return {}
        times = fallback_times or [
            start + (end - start) * index / 20.0 for index in range(21)
        ]
        pitch = sound.to_pitch(time_step=0.01)
        intensity = sound.to_intensity(minimum_pitch=50.0, time_step=0.01)
        formant = sound.to_formant_burg(time_step=0.01, max_number_of_formants=5)

        def sampled(values: list[float]) -> list[float]:
            return [float(value) for value in values]

        pitch_values = sampled([pitch.get_value_at_time(time) for time in times])
        intensity_values = sampled(
            [intensity.get_value_at_time(time) for time in times]
        )
        formant_tracks = {
            "f1": FeatureTrack(
                "f1",
                times,
                sampled(
                    [formant.get_value_at_time(1, time) for time in times]
                ),
                "Hz",
                1.3,
                120.0,
            ),
            "f2": FeatureTrack(
                "f2",
                times,
                sampled(
                    [formant.get_value_at_time(2, time) for time in times]
                ),
                "Hz",
                1.3,
                180.0,
            ),
            "f3": FeatureTrack(
                "f3",
                times,
                sampled(
                    [formant.get_value_at_time(3, time) for time in times]
                ),
                "Hz",
                0.9,
                220.0,
            ),
        }
        return {
            "pitch": FeatureTrack("pitch", times, pitch_values, "Hz", 0.9, 18.0),
            "intensity": FeatureTrack(
                "intensity",
                times,
                intensity_values,
                "dB",
                0.8,
                4.0,
            ),
            **formant_tracks,
        }
    except Exception:
        return {}


def extract_phone_tracks(
    path: str | Path,
    phones: list[PhoneSpec],
    *,
    learner: bool,
) -> list[PhoneFeatureTracks]:
    sound = read_wav(path)
    result: list[PhoneFeatureTracks] = []
    for index, phone in enumerate(phones, start=1):
        start = phone.learner_start if learner else phone.reference_start
        end = phone.learner_end if learner else phone.reference_end
        if start is None or end is None:
            continue
        segment = _slice(sound, start, end)
        tracks = _basic_tracks(segment, sound.sample_rate, start)
        fallback_times = tracks.get("rms").times if tracks.get("rms") else []
        tracks.update(
            _parselmouth_tracks(path, start, end, fallback_times)
        )
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
    return result
