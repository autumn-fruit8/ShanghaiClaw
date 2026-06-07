"""Trend utilities shared across 7S skills.

Provides pure math helpers for rolling OLS trend computation.
Used by:
  - strategy.py (S4) for LDev signal
  - draw_log_chart.py (Logarithm) for rolling trend curve
"""
from __future__ import annotations

import numpy as np
from scipy.stats import linregress

# Default rolling window: 1250 trading days ≈ 5 years
_DEFAULT_WINDOW_DAYS = 2500
_TREND_MIN_WINDOW = 500


def calc_rolling_trend_value(
    vals: np.ndarray,
    idx: int,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> float:
    """Compute the rolling OLS trend value at a single index point.

    For index ``idx``, fits OLS on log(vals[start:idx]) where
    ``start = max(0, idx - window_days)``, then projects one step forward
    to get the expected total_return level at index ``idx``.

    Returns ``exp(expected_log)`` = expected total_return level.
    Returns ``NaN`` if there are fewer than ``TREND_MIN_WINDOW`` (500)
    data points before ``idx``.

    Args:
        vals: 1D array of total_return values (prices).
        idx: Zero-based index to compute trend at.
        window_days: Number of trading days for the OLS lookback.

    Returns:
        Expected total_return value at ``idx``, or NaN if insufficient data.
    """
    if idx < _TREND_MIN_WINDOW:
        return float("nan")

    start = max(0, idx - window_days)
    y = np.log(vals[start : idx + 1])
    if len(y) < 2:
        return float("nan")

    x = np.arange(len(y))
    slope, intercept, _, _, _ = linregress(x, y)
    expected_log = intercept + slope * (len(y) - 1)
    return float(np.exp(expected_log))


def calc_rolling_trend_curve(
    vals: np.ndarray,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> np.ndarray:
    """Compute rolling OLS trend curve over the entire array.

    Returns an array of the same length as ``vals``, where each element
    is the rolling trend value at that index. Early indices (before
    ``TREND_MIN_WINDOW``) are filled with NaN.

    Args:
        vals: 1D array of total_return values.
        window_days: Number of trading days for the OLS lookback.

    Returns:
        Array of rolling trend values (same length as vals).
    """
    result = np.full(len(vals), np.nan)
    for i in range(_TREND_MIN_WINDOW, len(vals)):
        result[i] = calc_rolling_trend_value(vals, i, window_days)
    return result
