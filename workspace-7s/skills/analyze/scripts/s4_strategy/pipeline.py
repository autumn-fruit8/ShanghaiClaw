from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def run_strategy_pipeline(
    df_input: pd.DataFrame,
    asset_meta: dict,
    strategy_name: str = "dca-7s",
    backtest_years: int = 10,
    force_strategy: bool = False,
) -> Optional[dict]:
    """Run the full backtest pipeline using the modular strategy system.

    Pipeline: signal_computer → tactic_engine → account_simulator.
    Output dict shape is compatible with StrategyEngine.analyze().

    Args:
        df_input: DataFrame with date + price column.
        asset_meta: {"symbol", "name", "strategy_class", ...}.
        strategy_name: Name of strategy in config/strategies/ (default dca-7s).
        backtest_years: Simulation window in years from latest date.
        force_strategy: If True, use strategy_name directly without routing override.
                        Used when user explicitly overrides strategy via --strategy flag.

    Returns:
        Result dict with keys: symbol, name, meta, data, trades, region.
        Same shape as StrategyEngine.analyze() for chart compatibility.
    """
    from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
    from skills.analyze.scripts.s4_strategy.signal_computer import compute_profile
    from skills.analyze.scripts.s4_strategy.tactic import apply_tactic
    from skills.backtest.scripts.account_simulator import simulate_account

    # --- Load strategy ---
    try:
        registry = StrategyRegistry()
        symbol = asset_meta.get("symbol", "")
        species = asset_meta.get("strategy_class", "STEADY")
        if force_strategy:
            # User explicitly overrode strategy — use directly, no routing override
            strategy = registry.load(strategy_name)
        elif symbol:
            # Auto-resolve: always use resolve_strategy_for_asset for symbol-level
            # routing (checks routing yaml for strategy + tactic overrides)
            strategy = registry.resolve_strategy_for_asset(symbol, species)
        else:
            strategy = registry.load(strategy_name)
    except ValueError as e:
        return None

    # --- Normalize df ---
    cfg = {
        "MA_BASELINE": 250,
        "MA_TACTICAL": 60,
        "RSI_WINDOW": 14,
        "TREND_MIN_WINDOW": 500,
    }
    from skills.analyze.scripts.s4_strategy.engine import StrategyEngine
    df = StrategyEngine._normalise(df_input, cfg)
    if df is None:
        return None

    # --- Compute signals ---
    signal_df = compute_profile(
        df, strategy.profile,
        symbol=asset_meta.get("symbol", ""),
        ma_baseline=cfg["MA_BASELINE"],
        ma_tactical=cfg["MA_TACTICAL"],
        rsi_window=cfg["RSI_WINDOW"],
        trend_min_window=cfg["TREND_MIN_WINDOW"],
    )
    if signal_df is None:
        return None

    # --- Inject S3 macro context (yield_pctile, vix_pctile) ---
    from skills.analyze.scripts.s4_strategy.s3_context import inject_s3_context
    signal_df = inject_s3_context(
        signal_df,
        symbol=asset_meta.get("symbol", ""),
        region=asset_meta.get("region", ""),
        species=species,
        tags=asset_meta.get("tags", []),
        sleeve=asset_meta.get("sleeve", ""),
    )

    # --- Inject valuation context (pe_pctile, div_pctile) as columns ---
    try:
        from skills.analyze.scripts.s4_strategy.valuation import get_valuation
        val = get_valuation(asset_meta.get("symbol", ""))
        if val:
            pp = val.get("pe_pctile")
            dp = val.get("div_pctile")
            if pp is not None:
                signal_df["pe_pctile"] = float(pp)
            if dp is not None:
                signal_df["div_pctile"] = float(dp)
    except Exception:
        pass

    # --- Apply tactic ---
    trades, signal_df = apply_tactic(signal_df, strategy.tactic)

    # --- Signal columns for trade log snapshot ---
    signal_columns = list(strategy.profile.get("indicators", {}).keys())
    # Add derived columns that are always useful
    for extra in ("vol_ratio", "vol_signal", "vol_memo", "ldev", "zscore", "rsi"):
        if extra not in signal_columns and extra in signal_df.columns:
            signal_columns.append(extra)

    # --- Simulate account ---
    params = strategy.params
    equity_df, metrics, trade_log = simulate_account(
        signal_df, trades,
        initial_cash=params.get("initial_cash", 100000.0),
        backtest_years=backtest_years,
        signal_columns=signal_columns,
    )

    # --- Build final signal string ---
    last = equity_df.iloc[-1]
    last_signal_type = last.get("signal_type", "NEUTRAL")
    last_signal_desc = last.get("signal_desc", "Observing")
    final_signal = f"[{last_signal_type}] {last_signal_desc}"
    pulse_type = str(last.get("pulse_type", "NEUTRAL"))
    pulse_desc = str(last.get("pulse_desc", "中性"))

    # --- Append valuation context (STEADY only) ---
    strat_class = asset_meta.get("strategy_class", "").upper()
    if strat_class in ("STEADY", "BOND"):
        try:
            from skills.analyze.scripts.s4_strategy.valuation import get_valuation
            val = get_valuation(asset_meta.get("symbol", ""))
            if val:
                parts = []
                pp = val.get("pe_pctile")
                if pp is not None:
                    parts.append(f"PE {pp:.0f}%ile")
                dp = val.get("div_pctile")
                if dp is not None:
                    parts.append(f"Div {dp:.0f}%ile")
                if parts:
                    final_signal += " | " + " ".join(parts)
        except Exception:
            pass

    # --- Extract series for chart generation ---
    dates = [str(pd.to_datetime(d).date()) for d in equity_df["date"].values]
    vals = equity_df["val"].values
    log_price = [float(np.log(v)) if v > 0 else 0.0 for v in vals]
    strategy_equity = [float(v) for v in equity_df["strategy_equity"].values]
    buyhold_value = [float(v) for v in equity_df["buyhold_value"].values]
    cash_array = [float(v) for v in equity_df["_cash_array"].values]
    shares_array = [float(v) for v in equity_df["_shares_array"].values]

    # Roll trend/sigma for chart (from situation.py compute)
    roll_trend = []
    roll_sigma = []
    if "log_dev" in equity_df.columns:
        # Reconstruct by computing rolling trend
        trend = np.zeros(len(equity_df))
        sigma = np.zeros(len(equity_df))
        from scipy.stats import linregress as _lr
        lookback = 1250
        v = equity_df["val"].values
        for i in range(len(v)):
            if i < 250:
                trend[i] = np.log(v[i])
                continue
            start = max(0, i - lookback)
            y = np.log(v[start:i])
            x = np.arange(len(y))
            slope, intercept, _, _, _ = _lr(x, y)
            trend[i] = intercept + slope * len(y)
            residuals = y - (intercept + slope * x)
            sigma[i] = float(np.std(residuals)) if len(residuals) > 1 else 1e-6
        roll_trend = [float(v) for v in trend]
        roll_sigma = [float(v) for v in sigma]
        equity_df["roll_trend"] = roll_trend
        equity_df["roll_sigma"] = roll_sigma

    # --- Build sim_actions (BUY/SELL events with price context) ---
    sim_actions = []
    for t in trades:
        verb = t.verb if hasattr(t, "verb") else t["verb"]
        label = t.label if hasattr(t, "label") else t.get("label", "")
        t_date = t.date if hasattr(t, "date") else t["date"]
        # Find price on trade date
        mask = equity_df["date"].astype(str).str[:10] == str(t_date)[:10]
        price = float(equity_df.loc[mask, "val"].iloc[0]) if mask.any() else 0.0
        sim_actions.append({
            "date": str(t_date)[:10],
            "action": verb,
            "desc": label,
            "price": price,
        })

    # --- Build trades with correct shares_delta/amt_delta from trade_log ---
    # trade_log from simulate_account has enriched trade dicts with
    # post-trade state (cash_after, position_value, total_equity) and
    # signal snapshots (signals dict)
    trade_dicts = []
    for tl in trade_log:
        td = {
            "type": tl.get("type", "?"),
            "desc": tl.get("desc", ""),
            "shares_delta": float(tl.get("shares_delta", 0)),
            "amt_delta": float(tl.get("amt_delta", 0)),
            "date": str(tl.get("date", ""))[:10],
            "val": float(tl.get("val", tl.get("price", 0))),
            "score": 0.0,
        }
        # Pass through post-trade state if available
        if tl.get("cash_after") is not None:
            td["cash_after"] = float(tl["cash_after"])
        if tl.get("position_value") is not None:
            td["position_value"] = float(tl["position_value"])
        if tl.get("total_equity") is not None:
            td["total_equity"] = float(tl["total_equity"])
        if tl.get("signals"):
            td["signals"] = tl["signals"]
        trade_dicts.append(td)

    result = {
        "strategy_name": strategy_name,
        "tactic_name": strategy.tactic.get("name", strategy.name),
        "symbol": asset_meta.get("symbol", ""),
        "name": asset_meta.get("name", ""),
        "pulse_type": pulse_type,
        "pulse_desc": pulse_desc,
        "meta": {
            "Type": strat_class,
            "Signal": final_signal,
            "Signal_Action": "{} | LDev:{:.2f}σ | RSI:{:.1f}".format(
                final_signal,
                float(last.get("ldev", last.get("log_dev", 0.0))),
                float(last.get("rsi", 50.0)),
            ),
            **metrics,
        },
        "data": equity_df,
        "trades": trade_dicts,
        "region": asset_meta.get("region", ""),
        "series": {
            "dates": dates,
            "log_price": log_price,
            "strategy_equity": strategy_equity,
            "buyhold_value": buyhold_value,
            "cash_array": cash_array,
            "shares_array": shares_array,
            "roll_trend": roll_trend,
            "roll_sigma": roll_sigma,
            "ma250": [float(v) for v in equity_df["sma_250"].values] if "sma_250" in equity_df.columns
                     else ([float(v) for v in equity_df["ma_base"].values] if "ma_base" in equity_df.columns else []),
        },
        "sim_actions": sim_actions,
    }

    return result


# ═══════════════════════════════════════════════════════════════════════
# Analyze pipeline — signal only, no backtest (for cron mode)
# ═══════════════════════════════════════════════════════════════════════

_SPECIES_ADVICE = {
    "STEADY": "持有（定投不择时）",
    "BOND":   "持有（债基不择时）",
    "VOLATILE": None,
    "MOMENTUM": None,
}


def _build_action_advice(species: str, sim_action: str, sim_desc: str,
                         pulse_type: str = "", ldev: float = 0) -> str:
    """Build scenario-based advice from signal + pulse (no position data needed).

    Returns conditional advice covering both held/not-held scenarios.
    Layer 2 stateless — never pretends to know your position.
    """
    base = _SPECIES_ADVICE.get(species.upper(), "")
    if base:
        return base

    if sim_action == "BUY":
        if pulse_type in ("OVERBOUGHT", "EXTREME_OB"):
            return f"若持仓: 趋势延续持有；若空仓: 估值偏高(LDev={ldev:+.1f}σ)追入需谨慎"
        elif pulse_type in ("OVERSOLD", "EXTREME_OS"):
            return f"若持仓: 继续持有；若空仓: 估值偏低(LDev={ldev:+.1f}σ)可分批建仓"
        else:
            return "若持仓: 继续持有；若空仓: 趋势成立可考虑入场"
    elif sim_action == "SELL":
        return "若持仓: 建议离场；若空仓: 勿入场"
    else:
        return "观望"


def _build_indicators(last: pd.Series, species: str) -> dict:
    """Extract key indicator values from the last signal row."""
    indicators = {}
    for col in ("ldev", "log_dev", "rsi", "zscore", "z_score", "ma60_pct",
                "roc", "adx", "ma_cross", "price_above_ma_200",
                "yield_pctile", "vix_pctile"):
        if col in last.index:
            v = last.get(col)
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                indicators[col] = float(v) if isinstance(v, (int, float, np.generic)) else v
    if "log_dev" in indicators and "ldev" not in indicators:
        indicators["ldev"] = indicators.pop("log_dev")
    if "z_score" in indicators and "zscore" not in indicators:
        indicators["zscore"] = indicators.pop("z_score")
    return indicators


def run_analyze_pipeline(
    df_input: pd.DataFrame,
    asset_meta: dict,
    strategy_name: str = "dca-7s",
) -> Optional[dict]:
    """Run signal analysis only (no backtest simulation).

    Pipeline: signal_computer → tactic_engine.
    Output dict shape compatible with run_strategy.py cron expectations.

    Args:
        df_input: DataFrame with date + price column.
        asset_meta: {"symbol", "name", "strategy_class", ...}.
        strategy_name: Name of strategy in config/strategies/.

    Returns:
        Result dict with keys: symbol, name, meta (Type, Signal, Signal_Action),
        data (df with signal columns), strategy (strategy info), region.
    """
    from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
    from skills.analyze.scripts.s4_strategy.signal_computer import compute_profile
    from skills.analyze.scripts.s4_strategy.tactic import apply_tactic

    try:
        registry = StrategyRegistry()
        symbol = asset_meta.get("symbol", "")
        species = asset_meta.get("strategy_class", "STEADY")
        if symbol:
            strategy = registry.resolve_strategy_for_asset(symbol, species)
        else:
            strategy = registry.load("dca-7s")
    except ValueError:
        return None

    # Normalize df (reuse StrategyEngine's static method)
    from skills.analyze.scripts.s4_strategy.engine import StrategyEngine
    cfg = {"MA_BASELINE": 250, "MA_TACTICAL": 60, "RSI_WINDOW": 14, "TREND_MIN_WINDOW": 500}
    df = StrategyEngine._normalise(df_input, cfg)
    if df is None:
        return None

    # Compute signals (fast mode: single LDev, no full OLS loop)
    signal_df = compute_profile(
        df, strategy.profile,
        symbol=asset_meta.get("symbol", ""),
        ma_baseline=cfg["MA_BASELINE"],
        ma_tactical=cfg["MA_TACTICAL"],
        rsi_window=cfg["RSI_WINDOW"],
        trend_min_window=cfg["TREND_MIN_WINDOW"],
        fast=True,
    )
    if signal_df is None:
        return None

    # --- Inject S3 macro context (yield_pctile, vix_pctile) ---
    from skills.analyze.scripts.s4_strategy.s3_context import inject_s3_context
    species_ctx = asset_meta.get("strategy_class", "").upper()
    signal_df = inject_s3_context(
        signal_df,
        symbol=asset_meta.get("symbol", ""),
        region=asset_meta.get("region", ""),
        species=species_ctx,
        tags=asset_meta.get("tags", []),
        sleeve=asset_meta.get("sleeve", ""),
    )

    # --- Inject valuation context (pe_pctile, div_pctile) as columns ---
    try:
        from skills.analyze.scripts.s4_strategy.valuation import get_valuation
        val = get_valuation(asset_meta.get("symbol", ""))
        if val:
            pp = val.get("pe_pctile")
            dp = val.get("div_pctile")
            if pp is not None:
                signal_df["pe_pctile"] = float(pp)
            if dp is not None:
                signal_df["div_pctile"] = float(dp)
    except Exception:
        pass

    # Apply tactic → get signal labels
    trades, signal_df = apply_tactic(signal_df, strategy.tactic)

    # Build final signal string from last row
    last = signal_df.iloc[-1]
    last_type = last.get("signal_type", "NEUTRAL")
    last_desc = last.get("signal_desc", "Observing")
    last_action = last.get("sim_action", "HOLD")
    last_label = last.get("sim_desc", "")
    final_signal = f"[{last_type}] {last_desc}"
    pulse_type = str(last.get("pulse_type", "NEUTRAL"))
    pulse_desc = str(last.get("pulse_desc", "中性"))

    # Build species-specific indicator summary (used by advice and meta)
    indicators = _build_indicators(last, species)

    # Build action advice (scenario-based, no position data)
    advice = _build_action_advice(species, last_action, last_label,
                                  pulse_type=pulse_type,
                                  ldev=indicators.get("ldev", 0))

    # Build meta
    from skills.analyze.scripts.s4_strategy.alignment import classify_alignment
    alignment = classify_alignment(pulse_type, last_action)
    meta = {
        "Type": species,
        "Signal": final_signal,
        "Signal_Action": f"{advice} | {final_signal}",
        "Pulse": f"{pulse_type} ({pulse_desc})",
        "Alignment": alignment,
        "strategy": strategy.name,
        "advice": advice,
        "tactic_action": last_action,
        "tactic_label": last_label,
        "indicators": indicators,
    }

    # Append valuation context (STEADY/BOND)
    if species in ("STEADY", "BOND"):
        try:
            from skills.analyze.scripts.s4_strategy.valuation import get_valuation
            val = get_valuation(asset_meta.get("symbol", ""))
            if val:
                parts = []
                pp = val.get("pe_pctile")
                if pp is not None:
                    parts.append(f"PE {pp:.0f}%ile")
                dp = val.get("div_pctile")
                if dp is not None:
                    parts.append(f"Div {dp:.0f}%ile")
                if parts:
                    final_signal += " | " + " ".join(parts)
                    meta["Signal"] = final_signal
                    meta["Signal_Action"] = f"{advice} | {final_signal}"
        except Exception:
            pass

    return {
        "symbol": asset_meta.get("symbol", ""),
        "name": asset_meta.get("name", ""),
        "strategy_name": strategy.name,
        "tactic_name": strategy.tactic.get("name", strategy.name),
        "pulse_type": pulse_type,
        "pulse_desc": pulse_desc,
        "meta": meta,
        "data": signal_df,
        "region": asset_meta.get("region", ""),
    }
