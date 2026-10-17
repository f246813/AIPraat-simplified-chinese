"""NumPy adaptation of VowSpace's Lobanov zscore; see NOTICE.md and LICENSE.md."""
from __future__ import annotations

import numpy as np


def fit_lobanov(values):
    """Fit one formant, using pandas/R's sample standard deviation (ddof=1)."""
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or data.size < 2 or not np.isfinite(data).all():
        raise ValueError("Lobanov fitting requires at least two finite values.")
    mean, std = float(data.mean()), float(data.std(ddof=1))
    if not np.isfinite(mean) or not np.isfinite(std) or std <= 0:
        raise ValueError("Lobanov calibration has zero or nonfinite variance.")
    return mean, std


def transform_lobanov(values, mean, std):
    """Apply frozen parameters; no fitting, grouping, clipping or rescaling."""
    if not np.isfinite(mean) or not np.isfinite(std) or std <= 0:
        raise ValueError("Invalid frozen Lobanov parameters.")
    return (np.asarray(values, dtype=float) - mean) / std
