"""Windowed, one-sided FFT measurements with explicit common-band settings."""
from __future__ import annotations

import math
import numpy as np

PROTOCOL = "acoustic-extraction-v1"
ENERGY_ALGORITHM = "one-sided-fft-squared-magnitude-v1"


def settings(sample_rate: int, requested: dict | None = None) -> dict:
    actual = dict(window="hann", frame_sec=0.025, hop_sec=0.01,
                  band_low_hz=0.0, band_high_hz=sample_rate / 2.0, cutoff_hz=2000.0)
    requested = dict(requested or {})
    unknown = requested.keys() - actual.keys()
    if unknown:
        raise ValueError(f"Unknown spectral settings: {sorted(unknown)}")
    actual.update(requested)
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if actual["window"] not in ("hann", "rectangular"):
        raise ValueError("window must be hann or rectangular")
    for key in ("frame_sec", "hop_sec", "band_low_hz", "band_high_hz", "cutoff_hz"):
        actual[key] = float(actual[key])
        if not math.isfinite(actual[key]):
            raise ValueError(f"{key} must be finite")
    if actual["frame_sec"] <= 0 or actual["hop_sec"] <= 0:
        raise ValueError("frame_sec and hop_sec must be positive")
    if not 0 <= actual["band_low_hz"] < actual["band_high_hz"] <= sample_rate / 2:
        raise ValueError("common analysis band must lie within the source Nyquist frequency")
    if actual["cutoff_hz"] < 0:
        raise ValueError("cutoff_hz must be nonnegative")
    actual.update(sample_rate_hz=sample_rate, nyquist_hz=sample_rate / 2,
                  band_selection="closed-bin-centres", one_sided_weights="DC=1, Nyquist=1 (even N), interior=2",
                  energy_normalization="sum(weight * abs(rfft(window * frame))**2) / N**2",
                  window_definition="numpy.hanning symmetric" if actual["window"] == "hann" else "ones",
                  common_band_explicit="band_low_hz" in requested and "band_high_hz" in requested)
    return actual


def measure(frame: np.ndarray, sample_rate: int, actual: dict) -> dict:
    result = dict(high_frequency_ratio=float("nan"), spectral_centroid=float("nan"),
                  ratio_reason="", centroid_reason="", total_energy=None, high_energy=None)
    if not frame.size:
        result.update(ratio_reason="empty", centroid_reason="empty")
        return result
    if not np.all(np.isfinite(frame)):
        reason = "nan_samples" if np.any(np.isnan(frame)) else "infinite_samples"
        result.update(ratio_reason=reason, centroid_reason=reason)
        return result
    if not np.any(frame):
        result.update(ratio_reason="silence", centroid_reason="silence", total_energy=0.0, high_energy=0.0)
        return result
    window = np.hanning(frame.size) if actual["window"] == "hann" else np.ones(frame.size)
    spectrum = np.abs(np.fft.rfft(frame * window))
    frequencies = np.fft.rfftfreq(frame.size, d=1.0 / sample_rate)
    weights = np.full(spectrum.size, 2.0)
    weights[0] = 1.0
    if frame.size % 2 == 0:
        weights[-1] = 1.0
    energy = weights * spectrum ** 2 / frame.size ** 2
    band = (frequencies >= actual["band_low_hz"]) & (frequencies <= actual["band_high_hz"])
    high = band & (frequencies >= actual["cutoff_hz"])
    total = float(np.sum(energy[band]))
    high_energy = float(np.sum(energy[high]))
    result.update(total_energy=total if math.isfinite(total) else None,
                  high_energy=high_energy if math.isfinite(high_energy) else None)
    amplitude = float(np.sum(spectrum[band]))
    if math.isfinite(amplitude) and amplitude > 0:
        result["spectral_centroid"] = float(np.sum(frequencies[band] * spectrum[band]) / amplitude)
    else:
        result["centroid_reason"] = "zero_band_energy" if amplitude == 0 else "nonfinite_spectrum"
    if actual["cutoff_hz"] >= actual["band_high_hz"] or not np.any(high):
        result["ratio_reason"] = "no_high_band"
    elif not math.isfinite(total) or not math.isfinite(high_energy):
        result["ratio_reason"] = "nonfinite_spectrum"
    elif total == 0:
        result["ratio_reason"] = "zero_band_energy"
    else:
        result["high_frequency_ratio"] = high_energy / total
    return result
