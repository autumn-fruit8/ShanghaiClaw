"""
S2 Situation — Raw indicator calculation.

Computes LDev, Z-Score, RSI, MA250, MA60 from price history.
Algorithms are copied exactly from Jarvis strategy_engine.py
to ensure identical output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import linregress

from skills.analyze.models.situation_result import SituationResult


# ---------------------------------------------------------------------------
# Config defaults — must match config.yaml strategy.global block
# ---------------------------------------------------------------------------
_DEFAULT_GLOBAL = {
    "MA_BASELINE":       250,
    "MA_TACTICAL":        60,
    "RSI_WINDOW":         14,
    "TREND_MIN_WINDOW":  500,
}


def _from_config(config: dict) -> dict:
    """Merge config dict with defaults."""
    g = config.get("global", {}) if isinstance(config, dict) else {}
    d = _DEFAULT_GLOBAL
    return {
        "MA_BASELINE":      g.get("ma_baseline",      d["MA_BASELINE"]),
        "MA_TACTICAL":      g.get("ma_tactical",      d["MA_TACTICAL"]),
        "RSI_WINDOW":       g.get("rsi_window",       d["RSI_WINDOW"]),
        "TREND_MIN_WINDOW": g.get("trend_min_window", d["TREND_MIN_WINDOW"]),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rolling_trend(vals: np.ndarray, idx: int, lookback: int = 1250,
                   min_window: int = 250) -> tuple:
    """
    Compute LDev at index idx using a 1250-day lookback OLS on log prices.
    Mirrors Jarvis _rolling_trend() exactly.

    Returns (ldev, expected_log, sigma).
    """
    if idx < min_window:
        return 0.0, np.log(vals[idx]), 0.0

    start = max(0, idx - lookback)
    y = np.log(vals[start:idx])
    x = np.arange(len(y))

    slope, intercept, _, _, _ = linregress(x, y)
    residuals = y - (intercept + slope * x)
    sigma = np.std(residuals)
    if sigma == 0:
        sigma = 1e-6

    expected = intercept + slope * len(y)
    ldev = (np.log(vals[idx]) - expected) / sigma
    return ldev, expected, sigma


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def compute_indicators(
    df: pd.DataFrame,
    symbol: str,
    params: Optional[dict] = None,
    fast: bool = False,
) -> SituationResult:
    """
    Compute all raw indicators from price history.
    Algorithm is identical to Jarvis strategy_engine.py lines 194-245.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: date, val (price/nav). Other columns ignored.
    symbol : str
        Asset symbol for the result.
    params : dict, optional
        Strategy config dict. Falls back to defaults.

    Returns
    -------
    SituationResult
        Latest-row indicator values + full df with appended indicator columns.
    """
    if df is None or df.empty:
        raise ValueError(f"compute_indicators: df is empty for {symbol}")

    cfg = _from_config(params or {})

    # --- Normalise columns ---
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # Skip rename if val already exists (caller such as strategy.py
        # may have already normalised the price column to "val")
        if "val" not in df.columns:
            for src in ("total_return", "close", "Close"):
                if src in df.columns:
                    df_try = df.drop(columns=["val"], errors="ignore")  # avoid duplicate col name
                    df_try = df_try.rename(columns={src: "val"})
                    # Drop NaN rows; if too few remain, try next column
                    df_try = df_try.dropna(subset=["val"]).reset_index(drop=True)
                    if len(df_try) >= cfg.get("MA_BASELINE", 250):
                        df = df_try
                        break

        if "val" not in df.columns:
            raise ValueError(f"compute_indicators: no price column found for {symbol}")

        # Normalize val to a clean Series (handles MultiIndex edge case where
        # df["val"] accidentally returns a 1-col DataFrame instead of a Series)
        if df["val"].ndim > 1:
            df["val"] = df["val"].iloc[:, 0]

    if df["val"].notna().sum() == 0:
        raise ValueError(f"compute_indicators: val column has no valid (non-NaN) values for {symbol}")

    if len(df) < cfg["MA_BASELINE"]:
        raise ValueError(
            f"compute_indicators: insufficient history for {symbol} "
            f"(got {len(df)}, need >= {cfg['MA_BASELINE']})"
        )

    # --- Vectorised indicators (Jarvis lines 195-210) ---

    # log(price)
    df["log_val"] = np.log(df["val"])

    # MA250
    df["ma_base"] = df["val"].rolling(cfg["MA_BASELINE"]).mean()

    # MA60
    df["ma_tactical"] = df["val"].rolling(cfg["MA_TACTICAL"]).mean()

    # Z-Score: expanding normalisation of (price - MA250) / MA250 bias
    bias = (df["val"] - df["ma_base"]) / df["ma_base"]
    df["z_score"] = (
        (bias - bias.expanding().mean())
        / bias.expanding().std().replace(0, 1e-6)
    )

    # RSI — EWM-based (matches Jarvis exactly)
    delta = df["val"].diff()
    gain = delta.where(delta > 0, 0.0).ewm(com=cfg["RSI_WINDOW"] - 1).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(com=cfg["RSI_WINDOW"] - 1).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, 1e-6)))

    # --- Per-row LDev loop (Jarvis lines 213-245) ---
    n = len(df)
    vals = df["val"].values
    log_devs = np.zeros(n)
    roll_trends = np.zeros(n)
    roll_sigmas = np.zeros(n)

    start_sim_idx = cfg["TREND_MIN_WINDOW"]

    if fast:
        # Fast mode: only compute LDev for the last row.
        # All other rows use placeholder zeros (they're only needed
        # for backtest charts, not for daily signal output).
        if n >= start_sim_idx:
            i = n - 1
            ld, rt, rs = _rolling_trend(vals, i, lookback=1250, min_window=cfg["TREND_MIN_WINDOW"])
            log_devs[i] = ld
            roll_trends[i] = rt
            roll_sigmas[i] = rs
        else:
            log_devs[-1] = 0.0
            roll_trends[-1] = np.log(vals[-1])
    else:
        for i in range(n):
            if i < start_sim_idx:
                roll_trends[i] = np.log(vals[i])
                ld = 0.0
            else:
                ld, rt, rs = _rolling_trend(vals, i, lookback=1250, min_window=cfg["TREND_MIN_WINDOW"])
                log_devs[i] = ld
                roll_trends[i] = rt
                roll_sigmas[i] = rs

    df["log_dev"] = log_devs
    df["roll_trend"] = roll_trends
    df["roll_sigma"] = roll_sigmas

    # MA60_pct: (price - MA60) / MA60 * 100
    df["ma60_pct"] = (df["val"] - df["ma_tactical"]) / df["ma_tactical"] * 100

    # --- Extract latest values ---
    last = df.iloc[-1]

    return SituationResult(
        symbol=symbol,
        ldev=float(last["log_dev"]) if not np.isnan(last["log_dev"]) else 0.0,
        z_score=float(last["z_score"]) if not np.isnan(last["z_score"]) else 0.0,
        rsi=float(last["rsi"]) if not np.isnan(last["rsi"]) else 50.0,
        ma_base=float(last["ma_base"]) if not np.isnan(last["ma_base"]) else 0.0,
        ma_tactical=float(last["ma_tactical"]) if not np.isnan(last["ma_tactical"]) else 0.0,
        ma60_pct=float(last["ma60_pct"]) if not np.isnan(last["ma60_pct"]) else 0.0,
        price_current=float(last["val"]) if not np.isnan(last["val"]) else 0.0,
        date_current=str(last["date"].date()) if hasattr(last["date"], "date") else str(last["date"]),
        df_with_indicators=df,
    )


# ---------------------------------------------------------------------------
# Pulse classification — species-independent statistical assessment
# ---------------------------------------------------------------------------

def _load_pulse_config() -> tuple[dict, dict]:
    """Load pulse thresholds and descriptions from config file.
    Falls back to hardcoded defaults if config is missing or invalid.
    """
    try:
        import yaml
        from config import CONFIG_ROOT
        path = CONFIG_ROOT / "pulse_thresholds.yaml"
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f)
            thresholds = data.get("thresholds", {})
            descriptions = data.get("descriptions", {})
            if thresholds and descriptions:
                return thresholds, descriptions
    except Exception:
        pass

    return _DEFAULT_PULSE_THRESHOLDS, _DEFAULT_PULSE_DESC


_DEFAULT_PULSE_THRESHOLDS = {
    "extreme_ob":   {"ldev": 3.0, "zscore": 2.0, "rsi": 0},
    "overbought":   {"ldev": 2.0, "zscore": 1.5, "rsi": 70},
    "strong":       {"ldev": 1.0, "zscore": 0.5, "rsi": 60},
    "weak":         {"ldev": -1.0, "zscore": -0.5, "rsi": 40},
    "oversold":     {"ldev": -2.0, "zscore": -1.5, "rsi": 30},
    "extreme_os":   {"ldev": -3.0, "zscore": -2.0, "rsi": 0},
}

_DEFAULT_PULSE_DESC: dict[str, str] = {
    "EXTREME_OB":   "极度超买",
    "OVERBOUGHT":   "超买",
    "STRONG":       "偏强",
    "NEUTRAL":      "中性",
    "WEAK":         "偏弱",
    "OVERSOLD":     "超卖",
    "EXTREME_OS":   "极度超卖",
}


def classify_pulse(ldev: float, zscore: float, rsi: float) -> tuple[str, str]:
    """Species-independent statistical pulse classification.

    Classifies price extremity based solely on indicator values.
    Species (STEADY/VOLATILE/MOMENTUM/BOND) does NOT affect pulse —
    it is a routing label that selects which strategy to apply.

    Priority: LDev > ZScore > RSI.

    Thresholds are loaded from config/pulse_thresholds.yaml
    (falling back to hardcoded defaults if missing).

    Args:
        ldev:   Log deviation from 1250-day OLS trend (in σ).
        zscore: Z-score from expanding normalization.
        rsi:    RSI(14) value.

    Returns:
        (pulse_type: str, pulse_desc: str)
    """
    t, desc = _load_pulse_config()

    if ldev > t["extreme_ob"]["ldev"] or zscore > t["extreme_ob"]["zscore"]:
        return ("EXTREME_OB", desc["EXTREME_OB"])
    if ldev < t["extreme_os"]["ldev"] or zscore < t["extreme_os"]["zscore"]:
        return ("EXTREME_OS", desc["EXTREME_OS"])

    if ldev > t["overbought"]["ldev"] or zscore > t["overbought"]["zscore"] or rsi > t["overbought"]["rsi"]:
        return ("OVERBOUGHT", desc["OVERBOUGHT"])
    if ldev < t["oversold"]["ldev"] or zscore < t["oversold"]["zscore"] or rsi < t["oversold"]["rsi"]:
        return ("OVERSOLD", desc["OVERSOLD"])

    if ldev > t["strong"]["ldev"] or zscore > t["strong"]["zscore"] or rsi > t["strong"]["rsi"]:
        return ("STRONG", desc["STRONG"])
    if ldev < t["weak"]["ldev"] or zscore < t["weak"]["zscore"] or rsi < t["weak"]["rsi"]:
        return ("WEAK", desc["WEAK"])

    return ("NEUTRAL", desc["NEUTRAL"])
