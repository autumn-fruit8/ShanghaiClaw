"""
signal_computer.py — Compute indicators per Signal Profile.

Reads a profile (dict of indicator specs), dispatches to situation.py
for core indicators, then computes extras (SMAs, MA cross, ROC, ADX, bools).

Usage:
    from skills.analyze.scripts.s4_strategy.signal_computer import compute_profile

    signal_df = compute_profile(df, profile, symbol="159207")
    # → df with columns: date, val, ldev, rsi, zscore, sma_50, sma_200, ...
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def compute_profile(
    df: pd.DataFrame,
    profile: dict,
    symbol: str = "",
    ma_baseline: int = 250,
    ma_tactical: int = 60,
    rsi_window: int = 14,
    trend_min_window: int = 500,
    fast: bool = False,
) -> Optional[pd.DataFrame]:
    """Compute all indicators defined in profile.

    Args:
        df: Raw DataFrame with date + one of (total_return, close, val, Close).
        profile: Dict from a loaded profile YAML, e.g. {"indicators": {"ldev": {...}, ...}}.
        symbol: Asset symbol (for error messages).
        ma_baseline: Rolling window for baseline MA (default 250).
        ma_tactical: Rolling window for tactical MA (default 60).
        rsi_window: RSI computation window (default 14).
        trend_min_window: Minimum data points for trend detection (default 500).

    Returns:
        DataFrame with all requested indicator columns, or None if insufficient data.
    """
    indicators = profile.get("indicators", {})

    # --- Step 1: Compute core indicators via situation.py ---
    from skills.analyze.scripts.situation import compute_indicators

    # Build params dict for situation.py
    params = {
        "global": {
            "ma_baseline": ma_baseline,
            "ma_tactical": ma_tactical,
            "rsi_window": rsi_window,
            "trend_min_window": trend_min_window,
        }
    }
    try:
        situation = compute_indicators(df, symbol, params=params, fast=fast)
    except (ValueError, Exception) as e:
        return None

    result_df = situation.df_with_indicators.copy()

    # --- Step 2: Normalize column names (profile convention) ---
    # situation.py produces: log_dev, z_score, ma_base, ma_tactical, rsi, ma60_pct
    # We want: ldev, zscore, sma_250, sma_60, rsi, price_above_ma_60

    if "log_dev" in result_df.columns:
        result_df["ldev"] = result_df["log_dev"]
    if "z_score" in result_df.columns:
        result_df["zscore"] = result_df["z_score"]
    if "ma_base" in result_df.columns:
        result_df["sma_250"] = result_df["ma_base"]
    if "ma_tactical" in result_df.columns:
        result_df["sma_60"] = result_df["ma_tactical"]

    # --- Step 3: Compute profile extras ---

    vals = result_df["val"].values if "val" in result_df.columns else None

    # SMAs (extra windows like 20, 50, 200)
    sma_windows = indicators.get("sma", [])
    if isinstance(sma_windows, int):
        sma_windows = [sma_windows]
    for window in sma_windows:
        col = f"sma_{window}"
        if col not in result_df.columns and vals is not None:
            series = pd.Series(vals).rolling(window).mean()
            result_df[col] = series.values

    # price_above_ma (boolean indicators)
    ma_bools = indicators.get("price_above_ma", [])
    if isinstance(ma_bools, int):
        ma_bools = [ma_bools]
    for window in ma_bools:
        col = f"price_above_ma_{window}"
        if col not in result_df.columns:
            ma_col = f"sma_{window}"
            if ma_col in result_df.columns:
                result_df[col] = (result_df["val"] >= result_df[ma_col]).astype(bool)

    # MA cross (golden/death cross indicator)
    ma_cross = indicators.get("ma_cross")
    if ma_cross and vals is not None:
        fast = ma_cross.get("fast", 50)
        slow = ma_cross.get("slow", 200)
        fast_sma = pd.Series(vals).rolling(fast).mean()
        slow_sma = pd.Series(vals).rolling(slow).mean()
        # -1 = death cross, 0 = no cross, 1 = golden cross
        cross = np.zeros(len(vals))
        for i in range(1, len(vals)):
            if not np.isnan(fast_sma[i]) and not np.isnan(slow_sma[i]):
                if not np.isnan(fast_sma[i-1]) and not np.isnan(slow_sma[i-1]):
                    if fast_sma[i-1] <= slow_sma[i-1] and fast_sma[i] > slow_sma[i]:
                        cross[i] = 1  # golden cross
                    elif fast_sma[i-1] >= slow_sma[i-1] and fast_sma[i] < slow_sma[i]:
                        cross[i] = -1  # death cross
        result_df["ma_cross"] = cross

    # ROC (Rate of Change)
    roc_spec = indicators.get("roc")
    if roc_spec:
        roc_window = roc_spec.get("window", 20)
        if roc_window and vals is not None:
            roc_series = pd.Series(vals).pct_change(roc_window) * 100
            result_df["roc"] = roc_series.values
            result_df["roc"] = result_df["roc"].fillna(0.0)

    # ADX (Average Directional Index) — simplified
    adx_spec = indicators.get("adx")
    if adx_spec:
        adx_window = adx_spec.get("window", 14)
        if adx_window and vals is not None and len(vals) > adx_window + 2:
            direction = np.diff(np.log(vals.clip(min=1e-10)))
            up = np.maximum(direction, 0)
            down = -np.minimum(direction, 0)
            up_ema = pd.Series(up).ewm(span=adx_window).mean()
            down_ema = pd.Series(down).ewm(span=adx_window).mean()
            total = (up_ema + down_ema).replace(0, 1e-6)
            dx = abs(up_ema - down_ema) / total * 100
            adx_series = dx.rolling(adx_window).mean()
            adx_arr = np.zeros(len(vals))
            # Pad front to match length: diff reduces by 1, rolling reduces by adx_window
            pad_len = min(adx_window + 1, len(vals))
            if len(adx_series) + pad_len >= len(vals):
                adx_arr[pad_len:pad_len + len(adx_series)] = adx_series.values[:len(vals) - pad_len]
            result_df["adx"] = adx_arr

    # Slope (OLS log slope, annualized — matches momentum engine)
    slope_spec = indicators.get("slope")
    if slope_spec:
        slope_window = slope_spec.get("window", 20)
        if slope_window and vals is not None and len(vals) > slope_window:
            slope_arr = np.zeros(len(vals))
            for i in range(slope_window, len(vals)):
                y = np.log(vals[i - slope_window:i + 1].clip(min=1e-10))
                x = np.arange(len(y))
                slope, _ = np.polyfit(x, y, 1)
                slope_arr[i] = slope * 252
            result_df["slope"] = slope_arr

    # Volume ratio (optional — used by momentum-volume tactic)
    vol_ratio_spec = indicators.get("vol_ratio")
    if vol_ratio_spec and "volume" in df.columns:
        vol_window = vol_ratio_spec.get("window", 20)
        if vol_window:
            vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            vol_ma = vol.rolling(vol_window, min_periods=1).mean().replace(0, 1e-10)
        result_df["vol_ratio"] = (vol / vol_ma).values

    # Volume signal (structured, from rules)
    if "vol_ratio" in result_df.columns and "roc" in result_df.columns:
        from skills.analyze.scripts.s4_strategy.volume import compute_volume_signal
        vols = []
        memos = []
        for i in range(len(result_df)):
            vr = result_df["vol_ratio"].iloc[i]
            rc = result_df["roc"].iloc[i]
            sig, memo = compute_volume_signal(
                float(vr) if pd.notna(vr) else None,
                float(rc) if pd.notna(rc) else None,
            )
            vols.append(sig)
            memos.append(memo)
        result_df["vol_signal"] = vols
        result_df["vol_memo"] = memos

    # --- Step 4: Classify pulse per row (species-independent) ---
    from skills.analyze.scripts.situation import classify_pulse
    pulse_types = []
    pulse_descs = []
    for i in range(len(result_df)):
        ld = float(result_df["ldev"].iloc[i]) if "ldev" in result_df.columns else 0.0
        zs = float(result_df["zscore"].iloc[i]) if "zscore" in result_df.columns else 0.0
        rs = float(result_df["rsi"].iloc[i]) if "rsi" in result_df.columns else 50.0
        if np.isnan(ld):
            ld = 0.0
        if np.isnan(zs):
            zs = 0.0
        if np.isnan(rs):
            rs = 50.0
        ptype, pdesc = classify_pulse(ld, zs, rs)
        pulse_types.append(ptype)
        pulse_descs.append(pdesc)
    result_df["pulse_type"] = pulse_types
    result_df["pulse_desc"] = pulse_descs

    return result_df
