"""Momentum calculation engine — Simple, Slope, Composite methods.

Composite combines simple + slope via Z-score normalization
for a scale-independent ranking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calc_simple_momentum(df: pd.DataFrame, period: int = 20) -> float:
    """Simple momentum: (Pₜ - Pₜ₋ₙ) / Pₜ₋ₙ."""
    vals = df["total_return"].values
    if len(vals) < period + 1:
        return 0.0
    p_t = vals[-1]
    p_n = vals[-(period + 1)]
    return float((p_t - p_n) / p_n) if p_n != 0 else 0.0


def calc_slope_momentum(df: pd.DataFrame, period: int = 20, weighted: bool = False) -> float:
    """Slope momentum — OLS regression slope over N days (annualized)."""
    vals = df["total_return"].values
    if len(vals) < period + 1:
        return 0.0
    y = np.log(vals[-(period + 1):])
    x = np.arange(len(y))
    if weighted:
        w = np.linspace(0.5, 1.0, len(y))
        try:
            slope, _ = np.polyfit(x, y, 1, w=w)
        except np.linalg.LinAlgError:
            slope, _ = np.polyfit(x, y, 1)
    else:
        slope, _ = np.polyfit(x, y, 1)
    return float(slope * 252)


def _z_score(val: float, series: list[float]) -> float:
    arr = np.array(series, dtype=float)
    if len(arr) < 2 or np.std(arr) == 0:
        return 0.0
    return float((val - np.mean(arr)) / np.std(arr))


def calc_momentum(df: pd.DataFrame, method: str = "simple", period: int = 20,
                  weighted: bool = False) -> float:
    if method == "simple":
        return calc_simple_momentum(df, period)
    elif method == "slope":
        return calc_slope_momentum(df, period, weighted)
    else:
        raise ValueError(f"Unknown momentum method: {method}")


def calc_composite(simple_score: float, slope_score: float,
                   simple_all: list[float], slope_all: list[float]) -> float:
    z_s = _z_score(simple_score, simple_all)
    z_l = _z_score(slope_score, slope_all)
    return (z_s + z_l) / 2.0


def calc_multi_period(df: pd.DataFrame, method: str = "simple",
                      periods: list[int] | None = None,
                      weights: list[float] | None = None) -> float:
    """Multi-period composite — weighted average across multiple lookback windows.

    Default: short=20d (50%), medium=60d (30%), long=120d (20%).
    """
    periods = periods or [20, 60, 120]
    weights = weights or [0.5, 0.3, 0.2]
    scores = []
    for p in periods:
        if method == "simple":
            scores.append(calc_simple_momentum(df, p))
        else:
            scores.append(calc_slope_momentum(df, p))
    if not scores:
        return 0.0
    return float(sum(s * w for s, w in zip(scores, weights)) / sum(weights))


# ---------------------------------------------------------------------------
# Time-series — daily momentum scores for signal chart
# ---------------------------------------------------------------------------

def calc_daily_momentum(df: pd.DataFrame, method: str = "simple",
                        period: int = 20) -> list[tuple[str, float]]:
    """Compute momentum score at each trading day.

    Returns list of (date_str, score) tuples for rows where
    enough history exists. Oldest first.
    """
    dates = df["date"].values
    total = len(dates)
    if total < period + 1:
        return []

    vals = df["total_return"].values
    results: list[tuple[str, float]] = []

    for i in range(period, total):
        seg = df.iloc[:i + 1]
        if method == "simple":
            p_t = vals[i]
            p_n = vals[i - period]
            score = float((p_t - p_n) / p_n) if p_n != 0 else 0.0
        elif method == "slope":
            y = np.log(vals[i - period:i + 1])
            x = np.arange(len(y))
            slope, _ = np.polyfit(x, y, 1)
            score = float(slope * 252)
        else:
            score = 0.0
        date_str = str(pd.to_datetime(dates[i]).strftime("%Y-%m-%d"))
        results.append((date_str, score))

    return results


def calc_momentum_history(df: pd.DataFrame, step: int = 20, max_steps: int = 24,
                          period: int = 20) -> list[tuple[str, float, float]]:
    """Compute simple + slope momentum at regular intervals going back.

    Returns list of (date_label, simple_score, slope_score) tuples.
    """
    vals = df["total_return"].values
    dates = df["date"].values
    total = len(vals)
    if total < period + 1 + max_steps * step:
        max_steps = (total - period - 1) // step

    history: list[tuple[str, float, float]] = []
    for i in range(max_steps, -1, -1):
        end = total - i * step
        if end < period + 1 or end > total:
            continue
        segment = df.iloc[:end]
        simple = calc_simple_momentum(segment, period)
        slope = calc_slope_momentum(segment, period)
        date_label = str(pd.to_datetime(dates[end - 1]).strftime("%Y-%m"))
        history.append((date_label, simple, slope))

    return history
