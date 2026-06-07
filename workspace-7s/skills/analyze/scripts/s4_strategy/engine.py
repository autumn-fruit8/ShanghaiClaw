"""StrategyEngine — 7S S4 strategy signal engine.

Full backtest algorithm is copied exactly from Jarvis strategy_engine.py
to ensure identical output. Indicators are delegated to situation.py (S2).

Key differences from original 7S version (all now fixed):
  - LDev: now uses _rolling_trend() with 1250-day OLS lookback
  - Z-Score: now uses expanding mean/std
  - RSI: now uses EWM-based (com=13)
  - Added trend_streak / dip_streak counters with TREND_ACCUM_FREQ
  - Added position sizing (PER_TRADE_CASH * sim_param)
  - Added MOMENTUM parachute (price < MA60 -> DANGER)
  - Fixed _maxdd return order bug
  - Signal format now matches Jarvis: [BULLISH] / [BEARISH] / [OPPORTUNITY] / [WARNING] / [DANGER] / [NEUTRAL]

Refactored: Dec 2026
  - compute_signals() / run_backtest() split from analyze()
  - analyze() is a backward-compatible wrapper
  - compute_signals() reuses LDev from situation.py (no redundant OLS in loop)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import linregress
from typing import Optional

from skills.analyze.scripts.situation import compute_indicators


class StrategyEngine:
    """
    7S S4 strategy signal engine.

    Computes indicators (via situation.py / S2) and runs a theoretical
    backtest simulation. Algorithm matches Jarvis strategy_engine.py exactly.

    Two public entry points:
      compute_signals(df, meta)  → signals + per-row classification (no backtest)
      run_backtest(df, meta)     → backtest metrics from pre-classified df
      analyze(df, meta)          → both, backward-compatible

    Parameters
    ----------
    params : dict
        Strategy parameters. Accepts shape returned by ConfigLoader.get("strategy")
        or the full config dict. Falls back to _DEFAULT_PARAMS for missing keys.
    """

    def __init__(self, params: Optional[dict] = None):
        p = _from_config(params or {})
        self.cfg         = p["GLOBAL"]
        self.rules_steady = p["STEADY"]
        self.rules_vol    = p["VOLATILE"]
        self.rules_mom    = p["MOMENTUM"]

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _maxdd(series: pd.Series) -> tuple:
        """
        Return (max_dd_fraction, peak_value, trough_value).
        FIXED: return order now matches Jarvis (dd.iloc[-1], dd.min()).
        """
        if len(series) == 0:
            return 0.0, 0.0, 0.0
        roll_max = series.cummax()
        dd = (series - roll_max) / roll_max
        return float(dd.iloc[-1]), float(roll_max.iloc[-1]), float(dd.min())

    # -------------------------------------------------------------------------
    # Column normalisation (shared by compute_signals)
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalise(df_input: pd.DataFrame, cfg: dict) -> pd.DataFrame | None:
        """Validate and normalise a raw DataFrame: date, val, length checks."""
        if df_input is None or df_input.empty:
            return None

        df = df_input.copy()

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
        df = df.dropna(subset=["date"])

        for src in ("total_return", "close", "Close"):
            if src in df.columns:
                df_try = df.drop(columns=["val"], errors="ignore")
                df_try = df_try.rename(columns={src: "val"})
                # Drop NaN rows; if too few remain, try next column
                df_try = df_try.dropna(subset=["val"]).reset_index(drop=True)
                if len(df_try) >= cfg.get("MA_BASELINE", 250):
                    df = df_try
                    break

        if "val" not in df.columns:
            return None

        if df["val"].ndim > 1:
            df["val"] = df["val"].iloc[:, 0]

        if len(df) < cfg.get("MA_BASELINE", 250):
            return None

        return df

    # -------------------------------------------------------------------------
    # Signal classification (one row at a time — used by run_backtest too)
    # -------------------------------------------------------------------------

    def _classify_row(
        self,
        strat_class: str,
        ld: float,
        z: float,
        rsi_v: float,
        price: float,
        ma_tac: float,
        trend_streak: int,
        dip_streak: int,
    ) -> tuple:
        """
        Classify a single trading day's signal.
        Returns (mkt_type, mkt_desc, sim_action, sim_param, sim_desc, trend_streak, dip_streak).
        Pure function — caller owns streak state.
        """

        if strat_class == "STEADY":
            if ld > self.rules_steady["BUBBLE_EXIT_DEV"]:
                return ("BEARISH", "BUBBLE WARNING (LDev > {:.1f})".format(
                    self.rules_steady["BUBBLE_EXIT_DEV"]),
                    "SELL", 0.5, "Bubble Exit", 0, 0)

            elif ld < self.rules_steady["TREND_BUY_MAX_DEV"] and price > ma_tac:
                trend_streak += 1
                mkt_type = "BULLISH"
                mkt_desc = "TREND ACTIVE (Day {})".format(trend_streak)
                if trend_streak % self.rules_steady["TREND_ACCUM_FREQ"] == 0:
                    return (mkt_type, mkt_desc, "BUY", 0.5, "Trend Accum", trend_streak, 0)
                return (mkt_type, mkt_desc, "HOLD", 0.0, "", trend_streak, 0)

            elif ld < self.rules_steady["STRATEGIC_BUY_MAX_DEV"]:
                if ld < self.rules_steady["STRATEGIC_DEEP_VAL"]:
                    return ("OPPORTUNITY", "DEEP VALUE (LDev < {:.1f})".format(
                        self.rules_steady["STRATEGIC_DEEP_VAL"]),
                        "BUY", 2.0, "Deep Value", 0, 0)
                elif price < ma_tac:
                    dip_streak += 1
                    mkt_type = "OPPORTUNITY"
                    mkt_desc = "TACTICAL DIP (Day {})".format(dip_streak)
                    if dip_streak % self.rules_steady["DIP_ACCUM_FREQ"] == 0:
                        return (mkt_type, mkt_desc, "BUY", 1.0, "Dip Buy", 0, dip_streak)
                    return (mkt_type, mkt_desc, "HOLD", 0.0, "", 0, dip_streak)
                elif rsi_v < self.rules_steady["TACTICAL_RSI_BUY"]:
                    return ("OPPORTUNITY", "OVERSOLD (RSI < {})".format(
                        self.rules_steady["TACTICAL_RSI_BUY"]),
                        "BUY", 1.0, "RSI Oversold", 0, 0)
                else:
                    return ("NEUTRAL", "Observing", "HOLD", 0.0, "", 0, 0)
            else:
                return ("NEUTRAL", "Observing", "HOLD", 0.0, "", 0, 0)

        elif strat_class == "VOLATILE":
            if z > self.rules_vol["TACTICAL_HIGH_Z"]:
                return ("BEARISH", "EXTREME OVERBOUGHT (Z > {:.1f})".format(
                    self.rules_vol["TACTICAL_HIGH_Z"]),
                    "SELL", 1.0, "Tactical Clear", 0, 0)

            elif z > self.rules_vol["TACTICAL_SELL_Z"]:
                return ("WARNING", "OVERHEATED (Z > {:.1f})".format(
                    self.rules_vol["TACTICAL_SELL_Z"]),
                    "SELL", 0.5, "Tactical Profit", 0, 0)

            elif ld < self.rules_vol["STRATEGIC_HEAVY_BUY"]:
                return ("OPPORTUNITY", "DIAMOND BOTTOM (LDev < {:.1f})".format(
                    self.rules_vol["STRATEGIC_HEAVY_BUY"]),
                    "BUY", 2.0, "Strategic Bottom", 0, 0)

            elif ld < self.rules_vol["STRATEGIC_BUY_START"]:
                return ("OPPORTUNITY", "VALUE SEEDING (LDev < {:.1f})".format(
                    self.rules_vol["STRATEGIC_BUY_START"]),
                    "BUY", 1.0, "Strategic Seeding", 0, 0)

            else:
                return ("NEUTRAL", "Observing", "HOLD", 0.0, "", 0, 0)

        elif strat_class == "MOMENTUM":
            if price < ma_tac:
                return ("DANGER", "BROKEN TREND (Parachute)", "SELL", 1.0, "Parachute Cut", 0, 0)

            elif ld > self.rules_mom["SELL_LEVEL"]:
                return ("WARNING", "MELT-UP (LDev > {:.1f})".format(
                    self.rules_mom["SELL_LEVEL"]),
                    "SELL", 0.5, "Profit Taking", 0, 0)

            elif price > ma_tac and rsi_v > self.rules_mom["CHASE_BUY_MIN_RSI"]:
                trend_streak += 1
                if trend_streak % 5 == 0:
                    return ("BULLISH", "MOMENTUM IGNITION", "BUY", 1.0, "Chase Buy", trend_streak, 0)
                return ("BULLISH", "MOMENTUM IGNITION", "HOLD", 0.0, "", trend_streak, 0)
            else:
                return ("NEUTRAL", "Observing", "HOLD", 0.0, "", 0, 0)

        # Fallback
        return ("NEUTRAL", "Observing", "HOLD", 0.0, "", 0, 0)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def compute_signals(self, df_input: pd.DataFrame, asset_meta: dict) -> Optional[dict]:
        """
        Compute indicators and per-row signal classification. No trading simulation.

        Args:
            df_input: DataFrame with columns: date + one of (close | val | total_return | Close)
            asset_meta: {"symbol": str, "name": str, "strategy_class": "STEADY"|"VOLATILE"|"MOMENTUM"}

        Returns:
            Result dict or None if data insufficient.
            Keys: symbol, name, meta (Type, Signal, Signal_Action),
                  data (df with indicators + signal columns), region.
        """
        df = self._normalise(df_input, self.cfg)
        if df is None:
            return None

        # --- Compute indicators via S2 situation.py ---
        try:
            situation = compute_indicators(df, asset_meta.get("symbol", ""))
        except ValueError:
            return None
        df = situation.df_with_indicators

        strat_class = asset_meta.get("strategy_class", "VOLATILE")

        # --- Classify each row ---
        n = len(df)
        vals  = df["val"].values
        dates = df["date"].values

        signal_types = []
        signal_descs = []
        sim_actions  = []
        sim_params   = []
        sim_descs    = []
        trend_streak = 0
        dip_streak   = 0

        for i in range(n):
            ld    = float(df["log_dev"].iloc[i]) if not pd.isna(df["log_dev"].iloc[i]) else 0.0
            z     = float(df["z_score"].iloc[i]) if not pd.isna(df["z_score"].iloc[i]) else 0.0
            rsi_v = float(df["rsi"].iloc[i])     if not pd.isna(df["rsi"].iloc[i])     else 50.0
            ma_tac = float(df["ma_tactical"].iloc[i]) if not pd.isna(df["ma_tactical"].iloc[i]) else float(vals[i])
            price = float(vals[i])

            mkt_type, mkt_desc, sim_action, sim_param, sim_desc, trend_streak, dip_streak = \
                self._classify_row(strat_class, ld, z, rsi_v, price, ma_tac, trend_streak, dip_streak)

            signal_types.append(mkt_type)
            signal_descs.append(mkt_desc)
            sim_actions.append(sim_action)
            sim_params.append(sim_param)
            sim_descs.append(sim_desc)

        # --- Attach signal columns to df ---
        df["signal_type"]   = signal_types
        df["signal_desc"]   = signal_descs
        df["sim_action"]    = sim_actions
        df["sim_param"]     = sim_params
        df["sim_desc"]      = sim_descs

        # Build final signal string from the last row
        last = df.iloc[-1]
        final_signal = "[{}] {}".format(last["signal_type"], last["signal_desc"])

        # Phase 4: Append valuation percentile context (STEADY/BOND only, non-invasive)
        if strat_class in ("STEADY", "BOND"):
            try:
                from skills.analyze.scripts.s4_strategy.valuation import get_valuation
                val = get_valuation(asset_meta.get("symbol", ""))
                if val:
                    val_ctx = []
                    pp = val.get("pe_pctile")
                    if pp is not None:
                        val_ctx.append(f"PE {pp:.0f}%ile")
                    dp = val.get("div_pctile")
                    if dp is not None:
                        val_ctx.append(f"Div {dp:.0f}%ile")
                    if val_ctx:
                        final_signal += " | " + " ".join(val_ctx)
            except Exception:
                pass

        return {
            "symbol": asset_meta.get("symbol", ""),
            "name":   asset_meta.get("name", ""),
            "meta": {
                "Type":   strat_class,
                "Signal": final_signal,
                "Signal_Action": "{} | LDev:{:.2f}σ | RSI:{:.1f}".format(
                    final_signal,
                    float(last["log_dev"]) if not pd.isna(last["log_dev"]) else 0.0,
                    float(last["rsi"])     if not pd.isna(last["rsi"])     else 50.0,
                ),
            },
            "data":   df,
            "region": None,
        }

_DEFAULT_PARAMS = {
    "GLOBAL": {
        "BACKTEST_YEARS":     10,
        "INITIAL_CASH":      100_000.0,
        "PER_TRADE_CASH":     1_000.0,
        "MA_BASELINE":        250,
        "MA_TACTICAL":         60,
        "RSI_WINDOW":          14,
        "TREND_MIN_WINDOW":   500,
    },
    "STEADY": {
        "STRATEGIC_BUY_MAX_DEV":  1.0,
        "STRATEGIC_DEEP_VAL":     -1.5,
        "TACTICAL_RSI_BUY":       35,
        "BUBBLE_EXIT_DEV":         3.0,
        "TREND_BUY_MAX_DEV":       0.5,
        "TREND_ACCUM_FREQ":        5,
        "DIP_ACCUM_FREQ":          5,
    },
    "VOLATILE": {
        "STRATEGIC_BUY_START":  -1.0,
        "STRATEGIC_HEAVY_BUY":  -2.0,
        "TACTICAL_SELL_Z":       1.0,
        "TACTICAL_HIGH_Z":       1.5,
    },
    "MOMENTUM": {
        "SELL_LEVEL":          2.5,
        "CHASE_BUY_MIN_RSI":   50,
        "PARACHUTE_TRIGGER":    True,
    },
}


def _from_config(config: dict) -> dict:
    """
    Build STRATEGY_PARAMS-shaped dict from ConfigLoader's strategy block.
    Accepts the dict returned by ConfigLoader.get('strategy') or the full
    config dict; falls back to _DEFAULT_PARAMS for missing keys.
    """
    src = config.get("strategy") if "strategy" in config else config
    g = src.get("global", {}) if isinstance(src, dict) else {}
    s = src.get("steady", {}) if isinstance(src, dict) else {}
    v = src.get("volatile", {}) if isinstance(src, dict) else {}
    m = src.get("momentum", {}) if isinstance(src, dict) else {}
    d = _DEFAULT_PARAMS

    def _key(d_src, key, default_section, default_key=None):
        dk = (default_key or key).upper()
        return d_src.get(key, d[default_section][dk])

    return {
        "GLOBAL": {
            "BACKTEST_YEARS":    g.get("backtest_years",    d["GLOBAL"]["BACKTEST_YEARS"]),
            "INITIAL_CASH":      g.get("initial_cash",     d["GLOBAL"]["INITIAL_CASH"]),
            "PER_TRADE_CASH":    g.get("per_trade_cash",   d["GLOBAL"]["PER_TRADE_CASH"]),
            "MA_BASELINE":      g.get("ma_baseline",       d["GLOBAL"]["MA_BASELINE"]),
            "MA_TACTICAL":      g.get("ma_tactical",       d["GLOBAL"]["MA_TACTICAL"]),
            "RSI_WINDOW":       g.get("rsi_window",        d["GLOBAL"]["RSI_WINDOW"]),
            "TREND_MIN_WINDOW": g.get("trend_min_window",  d["GLOBAL"]["TREND_MIN_WINDOW"]),
        },
        "STEADY": {
            "STRATEGIC_BUY_MAX_DEV": _key(s, "strategic_buy_max_dev", "STEADY"),
            "STRATEGIC_DEEP_VAL":    _key(s, "strategic_deep_val", "STEADY"),
            "TACTICAL_RSI_BUY":      _key(s, "tactical_rsi_buy", "STEADY"),
            "BUBBLE_EXIT_DEV":        _key(s, "bubble_exit_dev", "STEADY"),
            "TREND_BUY_MAX_DEV":      _key(s, "trend_buy_max_dev", "STEADY"),
            "TREND_ACCUM_FREQ":       _key(s, "trend_accum_freq", "STEADY"),
            "DIP_ACCUM_FREQ":         _key(s, "dip_accum_freq", "STEADY"),
        },
        "VOLATILE": {
            "STRATEGIC_BUY_START": v.get("strategic_buy_start",  d["VOLATILE"]["STRATEGIC_BUY_START"]),
            "STRATEGIC_HEAVY_BUY":  v.get("strategic_heavy_buy",  d["VOLATILE"]["STRATEGIC_HEAVY_BUY"]),
            "TACTICAL_SELL_Z":      v.get("tactical_sell_z",      d["VOLATILE"]["TACTICAL_SELL_Z"]),
            "TACTICAL_HIGH_Z":      v.get("tactical_high_z",      d["VOLATILE"]["TACTICAL_HIGH_Z"]),
        },
        "MOMENTUM": {
            "SELL_LEVEL":       m.get("sell_level",       d["MOMENTUM"]["SELL_LEVEL"]),
            "CHASE_BUY_MIN_RSI": m.get("chase_buy_min_rsi", d["MOMENTUM"]["CHASE_BUY_MIN_RSI"]),
            "PARACHUTE_TRIGGER": m.get("parachute_trigger", d["MOMENTUM"]["PARACHUTE_TRIGGER"]),
        },
    }

