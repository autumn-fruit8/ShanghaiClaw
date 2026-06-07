"""
tactic_engine.py — Apply tactic rules to a signal series, generating trades.

Takes a DataFrame with signal columns (ldev, rsi, zscore, etc.) and a Tactic
(rules with conditions + actions), returns list of trades + signal timeline.

Usage:
    from skills.analyze.scripts.s4_strategy.tactic import apply_tactic

    trades, signal_df = apply_tactic(df, tactic)
    # trades = [Trade(date, verb, fraction, label), ...]
    # signal_df = df with signal_type/signal_desc/sim_action/sim_desc columns
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd


@dataclass
class Trade:
    date: str
    verb: str  # BUY / SELL / CLOSE / HOLD
    fraction: float
    label: str
    mode: str = "delta"  # "delta" (current) or "target" (target %)
    signal_values: dict = field(default_factory=dict)


def apply_tactic(
    df: pd.DataFrame,
    tactic: dict,
) -> tuple[list[Trade], pd.DataFrame]:
    """Apply tactic rules to signal DataFrame, return trades + enriched df.

    Args:
        df: DataFrame with signal columns (ldev, rsi, zscore, sma_N, etc.)
            Must have 'date' and 'val' columns.
        tactic: Dict from a loaded tactic YAML, e.g. {"rules": [...]}.

    Returns:
        (trades, signal_df)
        trades: List of Trade objects (only BUY/SELL events).
        signal_df: Copy of df with appended signal_type, signal_desc,
                   sim_action, sim_desc columns (every row classified).
    """
    rules = tactic.get("rules", [])
    default_action = tactic.get("default", {}).get("verb", "HOLD")
    result_df = df.copy()

    trades: list[Trade] = []
    cooldown_tracker: dict[str, int] = {}  # rule_id → last index

    n = len(result_df)
    signal_types = []
    signal_descs = []
    sim_actions = []
    sim_params = []
    sim_descs = []

    for i in range(n):
        row = result_df.iloc[i]
        row_signals = _extract_signals(row)

        fired = False
        for rule in rules:
            rule_id = rule["id"]
            label = rule.get("label", "")
            do = rule["do"]
            verb = do.get("verb", "HOLD")
            fraction = float(do.get("fraction", 0.0))
            cooldown = rule.get("cooldown", 0)

            # Check cooldown
            if cooldown > 0 and rule_id in cooldown_tracker:
                last_idx = cooldown_tracker[rule_id]
                if i - last_idx < cooldown:
                    continue

            # Evaluate conditions
            if _evaluate_conditions(rule["when"], row_signals):
                mode = do.get("mode", "delta")
                if verb in ("BUY", "SELL"):
                    trades.append(Trade(
                        date=str(row["date"])[:10] if hasattr(row["date"], "strftime") else str(row["date"]),
                        verb=verb,
                        fraction=fraction,
                        label=label,
                        mode=mode,
                        signal_values=row_signals,
                    ))
                    cooldown_tracker[rule_id] = i

                signal_types.append(rule.get("signal") or _verb_to_signal_type(verb))
                signal_descs.append(label)
                sim_actions.append(verb)
                sim_params.append(fraction)
                sim_descs.append(label)
                fired = True
                break  # first matching rule wins per row

        if not fired:
            # Default action from tactic config (HOLD / CLOSE / etc.)
            default_verb = default_action
            signal_types.append("NEUTRAL")
            signal_descs.append("Observing")
            sim_actions.append(default_verb)
            sim_params.append(0.0)
            sim_descs.append("")

    result_df["signal_type"] = signal_types
    result_df["signal_desc"] = signal_descs
    result_df["sim_action"] = sim_actions
    result_df["sim_param"] = sim_params
    result_df["sim_desc"] = sim_descs

    return trades, result_df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_signals(row: pd.Series) -> dict[str, Any]:
    """Extract known signal values from a DataFrame row."""
    signals = {}
    for col in row.index:
        if col in ("date", "val", "signal_type", "signal_desc", "sim_action",
                    "sim_param", "sim_desc", "log_val", "ma60_pct",
                    "log_dev", "z_score", "ma_base", "ma_tactical",
                    "strategy_equity", "_cash_array", "_shares_array",
                    "roll_trend", "roll_sigma"):
            continue
        try:
            v = row[col]
            if pd.isna(v) or np.isnan(v) if isinstance(v, float) else False:
                continue
            signals[col] = v
        except (ValueError, TypeError):
            continue
    return signals


def _evaluate_conditions(when: list, signals: dict) -> bool:
    """Evaluate a list of AND conditions against signal values."""
    for condition in when:
        if not isinstance(condition, dict):
            continue
        for indicator, op_spec in condition.items():
            val = signals.get(indicator)
            if val is None:
                return False
            if not _evaluate_op(val, op_spec):
                return False
    return True


def _evaluate_op(actual: Any, op_spec: dict) -> bool:
    """Evaluate a single operator specification against actual value."""
    try:
        actual_f = float(actual)
    except (ValueError, TypeError):
        return False

    for op, threshold in op_spec.items():
        if op == "lt":
            try:
                if not (actual_f < float(threshold)):
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "lte":
            try:
                if not (actual_f <= float(threshold)):
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "gt":
            try:
                if not (actual_f > float(threshold)):
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "gte":
            try:
                if not (actual_f >= float(threshold)):
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "eq":
            try:
                if isinstance(threshold, bool):
                    if actual_f != float(threshold):
                        return False
                elif abs(actual_f - float(threshold)) > 1e-9:
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "between":
            try:
                lo, hi = threshold
                if not (float(lo) <= actual_f < float(hi)):
                    return False
            except (ValueError, TypeError):
                return False
        else:
            return False
    return True


def _verb_to_signal_type(verb: str) -> str:
    mapping = {
        "BUY": "BULLISH",
        "SELL": "BEARISH",
        "CLOSE": "CAUTION",
        "HOLD": "NEUTRAL",
    }
    return mapping.get(verb, "NEUTRAL")
